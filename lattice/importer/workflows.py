"""The workflow layer of the Go importer (Spike B): from what Spike A extracted plus the
opt-in workflow facts of ``gocheck extract`` (``{"workflow": true}``), the *implemented*
flows and state machines of an app, as ``workflows.json`` (``lattice.workflows/1``).

This module is the pure part -- the dependency graph (D2), the flow synthesis (D3), the
value candidates and the repair rules (D4), the machine drafts (D6) and the output schema
(D9). :mod:`lattice.importer.workflow_run` runs the flows against the app (D5, D7) and
confirms the machines against the rows (D6). Nothing here starts a process, reads the
target, or infers anything from a name.

An edge ``(T.key; P -> C.p)`` exists iff C's path parameter ``p`` is compared to the key of
table T in the code (``param_uses``) and P creates T (``creates``); a body field compared
to a key is an edge as well (reported with ``param_source: "body"``). Flows are step
lists built from the edges: ``pair`` (one per edge, merged by skeleton), ``lifecycle``,
``effect``, ``owner_guard``, ``after_delete``, ``account``, ``machine``; ``guard`` and
``service`` flows come from a live run.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

SCHEMA = "lattice.workflows/1"
KIND_PRIORITY = (
    "pair",
    "lifecycle",
    "account",
    "owner_guard",
    "after_delete",
    "guard",
    "effect",
    "machine",
    "service",
)
MAX_STEPS = 8
MAX_FLOWS = 96
CHAIN_DEPTH = 4
ATTEMPTS_PER_STEP = 6
ATTEMPTS_PER_FLOW = 24
PRECONDITIONS = 3
READ_METHODS = ("GET",)
UUID_PREFIX = "00000000-0000-4000-8000-"
PROBE_STRINGS = (
    "{uuid}",
    "wf{n}@example.test",
    "UTC",
    "2026-01-01",
    "1.0.0",
    "https://example.test/",
)
DATE_TYPES = ("Date", "DateTime")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PARAM = re.compile(r"\{([^}]*)\}")


# ---- the input view -----------------------------------------------------------------------


def tpl(path: str) -> str:
    """A path with every parameter ``{}`` (the scoring identity)."""
    return _PARAM.sub("{}", path)


@dataclass(frozen=True)
class RouteV:
    """One route of the extraction, as the workflow layer reads it."""

    key: str  # "METHOD /v1/habits/{id}"
    method: str
    path: str
    skeleton: str  # "METHOD /v1/habits/{}"
    auth: str
    path_params: tuple[str, ...]
    params: tuple[Mapping[str, Any], ...]  # the non-path parameters
    status_codes: tuple[int, ...]
    refusals: frozenset[int]  # statuses of the route's raises and its middleware's guards
    owner: bool  # has owner evidence
    tables: frozenset[str]  # struct names
    file: str
    line: int
    handler: Mapping[str, Any] | None
    condition: str | None
    index: int  # registration order
    raises: tuple[Mapping[str, Any], ...] = ()

    @property
    def public(self) -> bool:
        return self.auth == "Public"


@dataclass(frozen=True)
class Edge:
    """``(table.key; producer -> consumer.param)``."""

    table: str
    key: str
    producer: str
    consumer: str
    param: str
    source: str  # path | body
    key_from: str  # a body field of the producer, or "server"
    alternatives: tuple[str, ...]
    evidence: tuple[Mapping[str, Any], ...]

    @property
    def id(self) -> str:
        raw = "|".join((self.table, self.producer, self.consumer, self.param, self.source))
        return "we-" + hashlib.sha256(raw.encode()).hexdigest()[:10]


class AppView:
    """The extraction (default keys plus the workflow keys), indexed."""

    def __init__(self, x: Mapping[str, Any]) -> None:
        self.x = x
        self.unread: list[dict[str, Any]] = []
        self.sql_of: dict[str, str] = {t["struct"]: t["table"] for t in x["tables"]}
        self.struct_of: dict[str, str] = {t["table"]: t["struct"] for t in x["tables"]}
        self.pk: dict[str, str] = {}
        for t in x["tables"]:
            pks = [c["name"] for c in t["columns"] if c["primary_key"]]
            if len(pks) == 1:
                self.pk[t["table"]] = pks[0]
        auth = x.get("auth") or {}
        self.users_struct: str | None = auth.get("users_table")
        self.users_sql: str | None = self.sql_of.get(self.users_struct or "")
        self.login_path: str | None = auth.get("login_path")
        mws = {m["func"]: m for m in x["middleware"]}
        routes: list[RouteV] = []
        for i, r in enumerate(x["routes"]):
            refusals = {s["status"] for s in r["raises"] if s["status"]}
            for g in r["guards"]:
                if g["status"]:
                    refusals.add(g["status"])
            for ref in r["middleware"]:
                for s in mws.get(ref["func"], {}).get("raises", []):
                    if s["status"]:
                        refusals.add(s["status"])
            path = r["path_template"]
            routes.append(
                RouteV(
                    key=f"{r['method']} {path}",
                    method=r["method"],
                    path=path,
                    skeleton=f"{r['method']} {tpl(path)}",
                    auth=r["auth"],
                    path_params=tuple(_PARAM.findall(path)),
                    params=tuple(p for p in r["params"] if p["source"] != "path"),
                    status_codes=tuple(r["status_codes"]),
                    refusals=frozenset(refusals),
                    owner=bool(r["owner_uses"]),
                    tables=frozenset(r["tables"]),
                    file=r["file"],
                    line=r["line"],
                    handler=r["handler"],
                    condition=r["condition"],
                    index=i,
                    raises=tuple(r["raises"]),
                )
            )
        self.all_routes = routes
        self.excluded_conditional = [r for r in routes if r.condition]
        self.routes = [r for r in routes if not r.condition]
        self.by_key = {r.key: r for r in self.routes}
        self.uses: dict[str, list[Mapping[str, Any]]] = {}
        for u in x.get("param_uses", []):
            self.uses.setdefault(u["route"], []).append(u)
        self.creates: dict[str, list[Mapping[str, Any]]] = {}
        for c in x.get("creates", []):
            self.creates.setdefault(c["route"], []).append(c)
        self.creators: dict[str, list[tuple[RouteV, Mapping[str, Any]]]] = {}
        for r in self.routes:
            for c in self.creates.get(r.key, []):
                self.creators.setdefault(c["table"], []).append((r, c))
        self.literals: dict[tuple[str, str, str], list[str]] = {}
        for lit in x.get("param_literals", []):
            self.literals[(lit["route"], lit["source"], lit["param"])] = list(lit["values"])
        self.facts: dict[tuple[str, int, str], Mapping[str, Any]] = {
            (f["file"], f["line"], f["column"]): f for f in x.get("status_facts", [])
        }

    def created_tables(self, r: RouteV) -> list[str]:
        out: list[str] = []
        for c in self.creates.get(r.key, []):
            if c["table"] not in out:
                out.append(c["table"])
        return out


# ---- D2: the dependency graph -------------------------------------------------------------


def build_edges(view: AppView) -> list[Edge]:
    """The edges of the app; edges with no producer go to ``view.unread``."""
    acc: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for c in view.routes:
        own = set(view.created_tables(c))
        for use in view.uses.get(c.key, []):
            if use["how"] != "compare":
                continue
            table, column = use["table"], use["column"]
            if view.pk.get(table) != column:
                continue
            if use["source"] == "path":
                if use["param"] not in c.path_params:
                    continue
            elif table in own:
                continue  # the route creates T itself: the field is its idempotency key
            producers = [(p, cr) for p, cr in view.creators.get(table, []) if p.key != c.key]
            if not producers:
                view.unread.append(
                    {
                        "file": use["file"],
                        "line": use["line"],
                        "what": "edge",
                        "reason": f"{c.key} {use['param']} is compared to {table}.{column} but "
                        "no route creates it (no producer)",
                    }
                )
                continue
            p, cr = producers[0]
            k = (table, p.key, c.key, use["param"], use["source"])
            slot = acc.setdefault(
                k,
                {
                    "key": column,
                    "key_from": cr["key_from"],
                    "alternatives": tuple(dict.fromkeys(q.key for q, _ in producers[1:])),
                    "uses": [],
                    "create": {"what": "create", "at": f"{cr['file']}:{cr['line']}"},
                },
            )
            ev = {"what": "param_use", "at": f"{use['file']}:{use['line']}"}
            if ev not in slot["uses"]:
                slot["uses"].append(ev)
    edges = [
        Edge(
            k[0],
            v["key"],
            k[1],
            k[2],
            k[3],
            k[4],
            v["key_from"],
            v["alternatives"],
            (*v["uses"], v["create"]),
        )
        for k, v in acc.items()
    ]
    return sorted(edges, key=lambda e: (e.table, e.producer, e.consumer, e.param, e.source))


# ---- D3: steps and flows ------------------------------------------------------------------


@dataclass
class Binding:
    param: str
    source: str  # path | body
    table: str
    from_step: int  # index (in the flow) of the producing step
    id_source: str = "client"  # client | response | list | seed (live: what produced it)
    value: Any = None


@dataclass
class StepSpec:
    """One request of a flow (the plan; the run fills the values)."""

    route: str
    caller: str | None
    role: str = "setup"
    bindings: list[Binding] = field(default_factory=list)
    inserted: str | None = None  # precondition | id_source


@dataclass
class Flow:
    kind: str
    steps: list[StepSpec]
    focus_from: int
    edge_refs: list[str] = field(default_factory=list)
    machine_refs: list[str] = field(default_factory=list)
    merged_kinds: list[str] = field(default_factory=list)
    status: str = "unverified"
    source: str = "static"
    reason: str = ""
    # the run's per-step results (filled by workflow_run)
    results: list[dict[str, Any]] = field(default_factory=list)
    await_steps: list[dict[str, Any]] = field(default_factory=list)

    def skeleton(self, view: AppView) -> list[str]:
        return [view.by_key[s.route].skeleton for s in self.steps]

    def callers(self) -> list[str | None]:
        return [s.caller for s in self.steps]

    def ident(self, view: AppView) -> str:
        raw = "|".join(
            (self.kind, " > ".join(self.skeleton(view)), ",".join(c or "-" for c in self.callers()))
        )
        return "wf-" + hashlib.sha256(raw.encode()).hexdigest()[:10]


class Planner:
    """Builds the plan: edges, flows, machines drafts."""

    def __init__(self, view: AppView, edges: list[Edge], users: Sequence[str]) -> None:
        self.view = view
        self.edges = edges
        self.users = list(users)
        self.by_consumer: dict[str, list[Edge]] = {}
        for e in edges:
            self.by_consumer.setdefault(e.consumer, []).append(e)
        for lst in self.by_consumer.values():
            lst.sort(key=lambda e: (_slot(view.by_key[e.consumer], e), e.param, e.source))
        self.by_table_consumer: dict[str, list[Edge]] = {}
        for e in edges:
            self.by_table_consumer.setdefault(e.table, []).append(e)

    # -- who calls

    def caller(self, r: RouteV, second: bool = False) -> str | None:
        if r.public or not self.users:
            return None
        if second and len(self.users) > 1:
            return self.users[1]
        return self.users[0]

    # -- chains

    def chain(
        self,
        route: str,
        steps: list[StepSpec] | None = None,
        provided: dict[str, int] | None = None,
        depth: int = 0,
    ) -> tuple[list[StepSpec], dict[str, int]] | None:
        """The steps that satisfy ``route``'s references, then ``route``; None when the
        references need more than ``CHAIN_DEPTH`` producers deep."""
        steps = [] if steps is None else steps
        provided = {} if provided is None else provided
        r = self.view.by_key[route]
        binds: list[Binding] = []
        for e in self.by_consumer.get(route, []):
            if e.table not in provided:
                if depth >= CHAIN_DEPTH or e.producer == route:
                    return None
                if self.chain(e.producer, steps, provided, depth + 1) is None:
                    return None
            binds.append(Binding(e.param, e.source, e.table, provided[e.table]))
        idx = len(steps)
        steps.append(StepSpec(route, self.caller(r), "setup", binds))
        for t in self.view.created_tables(r):
            provided.setdefault(t, idx)
        return steps, provided

    def _flow(self, kind: str, steps: list[StepSpec], focus: int, **kw: Any) -> Flow | None:
        if not steps:
            return None
        steps[focus].role = "focus"
        return Flow(kind, steps, focus, **kw)

    # -- the kinds

    def pairs(self) -> list[Flow]:
        out: dict[str, Flow] = {}
        for e in self.edges:
            got = self.chain(e.consumer)
            if got is None:
                continue
            steps, provided = got
            focus = provided.get(e.table)
            if focus is None:
                continue
            flow = self._flow("pair", steps, focus, edge_refs=[e.id])
            if flow is None:
                continue
            sk = (
                " > ".join(flow.skeleton(self.view))
                + "|"
                + ",".join(c or "-" for c in flow.callers())
            )
            if sk in out:
                prev = out[sk]
                prev.edge_refs.append(e.id)
                if focus > prev.focus_from:
                    prev.steps[prev.focus_from].role = "setup"
                    prev.focus_from = focus
                    prev.steps[focus].role = "focus"
            else:
                out[sk] = flow
        return list(out.values())

    def owner_guards(self, pairs: Iterable[Flow]) -> list[Flow]:
        if len(self.users) < 2:
            return []
        out: dict[str, Flow] = {}
        for p in pairs:
            last = self.view.by_key[p.steps[-1].route]
            if not last.owner or last.public:
                continue
            steps = [
                StepSpec(
                    s.route,
                    s.caller,
                    s.role,
                    [Binding(b.param, b.source, b.table, b.from_step) for b in s.bindings],
                )
                for s in p.steps
            ]
            steps[-1].caller = self.users[1]
            f = Flow("owner_guard", steps, p.focus_from, edge_refs=list(p.edge_refs))
            out.setdefault(f.ident(self.view), f)
        return list(out.values())

    def lifecycles(self) -> list[Flow]:
        """Per root table: the write steps in phase order -- create T, create its children,
        update T, delete the children, delete T."""
        out: list[Flow] = []
        roots: dict[str, str] = {}
        first: dict[str, str] = {}
        for e in self.edges:
            if e.source == "path":
                first.setdefault(e.table, e.producer)
        for table, producer in first.items():
            if not self.by_consumer.get(producer):
                roots[table] = producer
        for table in sorted(roots):
            children = [
                (c, first[c])
                for c in sorted(first)
                if c != table and any(b.table == table for b in self.by_consumer.get(first[c], []))
            ]
            phases = [pc for _, pc in children]
            phases += self._action_routes(table)
            for c, _ in children:
                phases += self._consumers_by_method(c, ("DELETE",))
            phases += self._consumers_by_method(table, ("DELETE",))
            steps: list[StepSpec] = []
            provided: dict[str, int] = {}
            if self.chain(roots[table], steps, provided) is None:
                continue
            focus = provided.get(table, 0)
            ok = True
            for route in phases:
                if any(s.route == route for s in steps):
                    continue
                if self.chain(route, steps, provided) is None:
                    ok = False
                    break
            if not ok:
                continue
            flow = self._flow("lifecycle", steps, focus)
            if flow is not None:
                out.append(flow)
        return out

    def _consumers_by_method(self, table: str, methods: tuple[str, ...]) -> list[str]:
        seen: list[str] = []
        for e in self.by_table_consumer.get(table, []):
            r = self.view.by_key[e.consumer]
            if e.source == "path" and r.method in methods and e.consumer not in seen:
                seen.append(e.consumer)
        return sorted(seen, key=lambda k: self.view.by_key[k].index)

    def _action_routes(self, table: str) -> list[str]:
        """PATCH/PUT routes on T's key, and POST routes on it that do not create T."""
        seen: list[str] = []
        for e in self.by_table_consumer.get(table, []):
            r = self.view.by_key[e.consumer]
            if e.source != "path" or e.consumer in seen:
                continue
            if r.method in ("PATCH", "PUT") or (
                r.method == "POST" and table not in self.view.created_tables(r)
            ):
                seen.append(e.consumer)
        return sorted(seen, key=lambda k: self.view.by_key[k].index)

    def after_deletes(self) -> list[Flow]:
        out: dict[str, Flow] = {}
        for table in sorted(self.by_table_consumer):
            deletes = self._consumers_by_method(table, ("DELETE",))
            reads = [k for k in self._consumers_by_method(table, READ_METHODS) if k not in deletes]
            reads += [
                e.consumer
                for e in self.by_table_consumer[table]
                if e.source == "path"
                and self.view.by_key[e.consumer].method == "GET"
                and e.consumer not in reads
            ]
            reads = sorted(set(reads), key=lambda k: self.view.by_key[k].index)
            for d in deletes:
                if not reads:
                    continue
                got = self.chain(d)
                if got is None:
                    continue
                steps, provided = got
                if self.chain(reads[0], steps, provided) is None:
                    continue
                focus = provided.get(table, 0)
                flow = self._flow("after_delete", steps, min(focus, len(steps) - 1))
                if flow is not None:
                    out.setdefault(flow.ident(self.view), flow)
        return list(out.values())

    def account(self) -> list[Flow]:
        v = self.view
        users_sql = v.users_sql
        register = next(
            (
                r
                for r in v.routes
                if r.method == "POST" and r.public and users_sql in v.created_tables(r)
            ),
            None,
        )
        login = next((r for r in v.routes if r.method == "POST" and r.path == v.login_path), None)
        if register is None or login is None or register is login:
            v.unread.append(
                {
                    "file": "",
                    "line": 0,
                    "what": "account",
                    "reason": "no public POST creates the users table and a login route together",
                }
            )
            return []
        steps = [StepSpec(register.key, None, "setup"), StepSpec(login.key, None, "focus")]
        return [Flow("account", steps, 1)]

    def effects(self) -> list[Flow]:
        v = self.view
        out: dict[str, Flow] = {}
        writers: list[str] = []
        for r in v.routes:
            if r.method == "GET" or r.public:
                continue  # an anonymous write has no declared user to read it back
            if v.creates.get(r.key) or self.by_consumer.get(r.key):
                writers.append(r.key)
        for w in sorted(writers, key=lambda k: v.by_key[k].index):
            got = self.chain(w)
            if got is None:
                continue
            base, provided = got
            wtables = set(v.by_key[w].tables) - ({v.users_struct} if v.users_struct else set())
            for r in v.routes:
                if r.method != "GET" or r.key == w:
                    continue
                if not (set(r.tables) & wtables):
                    continue
                steps = [
                    StepSpec(
                        s.route,
                        s.caller,
                        s.role,
                        [Binding(b.param, b.source, b.table, b.from_step) for b in s.bindings],
                    )
                    for s in base
                ]
                prov = dict(provided)
                if self.chain(r.key, steps, prov) is None:
                    continue
                if len(steps) != len(base) + 1:
                    continue  # the read needs producers the write chain does not hold
                flow = self._flow("effect", steps, len(base) - 1)
                if flow is not None:
                    out.setdefault(flow.ident(v), flow)
        return list(out.values())


def _slot(r: RouteV, e: Edge) -> int:
    if e.source == "path" and e.param in r.path_params:
        return r.path_params.index(e.param)
    return len(r.path_params)


# ---- the plan -----------------------------------------------------------------------------


def edge_row(view: AppView, e: Edge, id_source: str) -> dict[str, Any]:
    return {
        "id": e.id,
        "table": e.table,
        "key": e.key,
        "producer": e.producer,
        "consumer": e.consumer,
        "param": e.param,
        "param_source": e.source,
        "id_source": id_source,
        "alternatives": list(e.alternatives),
        "evidence": [dict(x) for x in e.evidence],
    }


def flow_name(view: AppView, flow: Flow) -> str:
    parts = []
    for s in flow.steps:
        r = view.by_key[s.route]
        segs = [p for p in r.path.split("/") if p]
        literals = [p for p in segs if not p.startswith("{")]
        verb = {
            "GET": "read",
            "POST": "create",
            "PUT": "update",
            "PATCH": "update",
            "DELETE": "delete",
        }.get(r.method, r.method.lower())
        noun = literals[-1] if literals else "root"
        if (
            r.method in ("POST", "PUT", "PATCH")
            and len(segs) >= 2
            and not segs[-1].startswith("{")
            and segs[-2].startswith("{")
            and len(literals) >= 2
        ):
            verb, noun = segs[-1], literals[-2]
        parts.append(f"{verb} {noun}")
    return " > ".join(parts)


def machine_drafts(view: AppView) -> list[dict[str, Any]]:
    """Machine candidates from the status writes (D6): (table, column) with two or more
    constant values; states, initial (create sites), static edges (update sites' from)."""
    writes = view.x.get("status_writes", [])
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for w in writes:
        groups.setdefault((w["table"], w["column"]), []).append(w)
    drafts: list[dict[str, Any]] = []
    for (table, column), ws in sorted(groups.items()):
        states = sorted({w["value"] for w in ws})
        if len(states) < 2:
            continue
        initial: set[str] = set()
        edges: dict[tuple[str, str], dict[str, Any]] = {}
        unread: list[dict[str, Any]] = []
        roots: dict[tuple[str, str], list[str]] = {}
        complete = True
        for w in ws:
            fact = view.facts.get((w["file"], w["line"], w["column"]))
            if fact is None:
                complete = False
                unread.append(
                    {
                        "file": w["file"],
                        "line": w["line"],
                        "what": "status_write",
                        "reason": "no create/update fact for this write",
                    }
                )
                continue
            if fact["kind"] == "create":
                initial.add(w["value"])
                continue
            for frm in fact["from"]:
                if frm == "*":
                    complete = False
                    unread.append(
                        {
                            "file": w["file"],
                            "line": w["line"],
                            "what": "edge",
                            "reason": f"the state {w['value']} is written from an "
                            "unknown state: no static map",
                        }
                    )
                    continue
                if frm == w["value"]:
                    continue
                e = edges.setdefault(
                    (frm, w["value"]), {"from": frm, "to": w["value"], "evidence": []}
                )
                for at in fact["from_at"] or []:
                    ev = {"what": "from", "at": f"{at['file']}:{at['line']}"}
                    if ev not in e["evidence"]:
                        e["evidence"].append(ev)
                ev = {"what": "write", "at": f"{w['file']}:{w['line']}"}
                if ev not in e["evidence"]:
                    e["evidence"].append(ev)
                roots.setdefault((frm, w["value"]), []).append(w["root"])
        raw = f"{table}.{column}"
        drafts.append(
            {
                "id": "wm-" + hashlib.sha256(raw.encode()).hexdigest()[:10],
                "table": table,
                "model": ws[0]["model"],
                "column": column,
                "states": states,
                "initial": sorted(initial),
                "static": complete and bool(edges),
                "edges": [
                    {
                        **edges[k],
                        "source": "static",
                        "observed": 0,
                        "roots": sorted(set(roots.get(k, []))),
                    }
                    for k in sorted(edges)
                ],
                "unread": unread,
            }
        )
    return drafts


@dataclass
class Plan:
    view: AppView
    edges: list[Edge]
    flows: list[Flow]
    machines: list[dict[str, Any]]
    capped: list[dict[str, Any]]
    users: list[str]


def plan(
    x: Mapping[str, Any],
    users: Sequence[str] = (),
    *,
    max_flows: int = MAX_FLOWS,
    max_steps: int = MAX_STEPS,
) -> Plan:
    """The static plan of an extraction: edges, flows (priority order, capped), machine
    drafts (machine flows included)."""
    view = AppView(x)
    edges = build_edges(view)
    pl = Planner(view, edges, users)
    machines = machine_drafts(view)
    pairs = pl.pairs()
    flows: list[Flow] = []
    flows += pairs
    flows += pl.lifecycles()
    flows += pl.account()
    flows += pl.owner_guards(pairs)
    flows += pl.after_deletes()
    flows += pl.effects()
    flows += machine_flows(pl, machines)
    return select_flows(view, flows, machines, max_flows, max_steps, users, edges)


def machine_flows(pl: Planner, machines: list[dict[str, Any]]) -> list[Flow]:
    """For a machine: a chain that leaves a row of its table (created by the table's first
    creator, whose write sets an initial state), then each route rooted at an update of the
    column whose from-state is a state a row can be in."""
    view = pl.view
    out: dict[str, Flow] = {}
    for m in machines:
        if not m["static"] or not view.creators.get(m["table"]):
            continue
        creator = view.creators[m["table"]][0][0].key
        for e in m["edges"]:
            for root in e["roots"]:
                if root not in view.by_key or root == creator:
                    continue
                steps: list[StepSpec] = []
                provided: dict[str, int] = {}
                if pl.chain(creator, steps, provided) is None:
                    continue
                if pl.chain(root, steps, provided) is None:
                    continue
                flow = pl._flow("machine", steps, len(steps) - 1, machine_refs=[m["id"]])
                if flow is not None:
                    out.setdefault(flow.ident(view), flow)
    return list(out.values())


def select_flows(
    view: AppView,
    flows: list[Flow],
    machines: list[dict[str, Any]],
    max_flows: int,
    max_steps: int,
    users: Sequence[str],
    edges: list[Edge],
) -> Plan:
    """Dedupe by (skeleton, callers) keeping the first kind in priority order, cap the
    steps and the flow count; the dropped go to ``capped``."""
    prio = {k: i for i, k in enumerate(KIND_PRIORITY)}

    def sk(f: Flow) -> str:
        return " > ".join(f.skeleton(view)) + "|" + ",".join(c or "-" for c in f.callers())

    ordered = sorted(
        flows,
        key=lambda f: (
            prio[f.kind],
            " > ".join(f.skeleton(view)),
            ",".join(c or "-" for c in f.callers()),
        ),
    )
    seen: dict[str, Flow] = {}
    capped: list[dict[str, Any]] = []
    for f in ordered:
        key = sk(f)
        if key in seen:
            if f.kind not in seen[key].merged_kinds and f.kind != seen[key].kind:
                seen[key].merged_kinds.append(f.kind)
            for ref in f.edge_refs:
                if ref not in seen[key].edge_refs:
                    seen[key].edge_refs.append(ref)
            for ref in f.machine_refs:
                if ref not in seen[key].machine_refs:
                    seen[key].machine_refs.append(ref)
            continue
        if len(f.steps) > max_steps:
            capped.append(
                {
                    "id": f.ident(view),
                    "kind": f.kind,
                    "skeleton": f.skeleton(view),
                    "reason": f"more than {max_steps} requests",
                }
            )
            continue
        seen[key] = f
    kept = list(seen.values())
    if len(kept) > max_flows:
        for f in kept[max_flows:]:
            capped.append(
                {
                    "id": f.ident(view),
                    "kind": f.kind,
                    "skeleton": f.skeleton(view),
                    "reason": f"more than {max_flows} flows",
                }
            )
        kept = kept[:max_flows]
    capped.sort(key=lambda c: (c["kind"], c["skeleton"], c["id"]))
    return Plan(view, edges, kept, machines, capped, list(users))


# ---- D4: values ---------------------------------------------------------------------------


@dataclass
class ValueCtx:
    """What a flow has sent and received so far, and its counters. A generated value (a
    fresh id, a probe) is made when it is *picked*, so the counter follows the requests."""

    flow_tag: str
    counter: int = 0
    requests: list[dict[str, Any]] = field(default_factory=list)  # earlier bodies
    responses: list[Any] = field(default_factory=list)  # earlier JSON answers
    picked: dict[tuple[str, int], Any] = field(default_factory=dict)

    def next_uuid(self) -> str:
        self.counter += 1
        return f"{UUID_PREFIX}{self.counter:012d}"

    def next_n(self) -> int:
        self.counter += 1
        return self.counter

    def new_step(self) -> None:
        """A new request: its placeholders are made afresh."""
        self.picked = {}

    def pick(self, name: str, index: int, value: Any) -> Any:
        """The value of candidate ``index`` of field ``name`` of this step (a placeholder
        is made once per step)."""
        key = (name, index)
        if key not in self.picked:
            if value == "{uuid}":
                self.picked[key] = self.next_uuid()
            elif isinstance(value, str) and "{n}" in value:
                self.picked[key] = value.replace("{n}", str(self.next_n()))
            else:
                self.picked[key] = value
        return self.picked[key]


def _coerce(value: str, typ: str | None) -> Any:
    if typ == "Int":
        try:
            return int(value)
        except ValueError:
            return None
    if typ == "Float":
        try:
            return float(value)
        except ValueError:
            return None
    if typ == "Bool":
        return {"true": True, "false": False}.get(value.lower())
    return value


def _dates_in(obj: Any, out: list[str]) -> None:
    if isinstance(obj, str) and _ISO_DATE.match(obj):
        out.append(obj)
    elif isinstance(obj, Mapping):
        for k in sorted(obj):
            _dates_in(obj[k], out)
    elif isinstance(obj, list):
        for v in obj:
            _dates_in(v, out)


def _bound_values(name: str, typ: str | None, ctx: ValueCtx) -> list[Any]:
    """A same-name field of an earlier request, then of an earlier response (top level or
    one level down); for a date type, the first ISO date of the earlier responses."""
    out: list[Any] = []
    for body in reversed(ctx.requests):
        if isinstance(body, Mapping) and name in body and body[name] not in out:
            out.append(body[name])
    for resp in reversed(ctx.responses):
        if isinstance(resp, Mapping):
            if name in resp and not isinstance(resp[name], (dict, list)) and resp[name] not in out:
                out.append(resp[name])
            for k in sorted(resp):
                sub = resp[k]
                if (
                    isinstance(sub, Mapping)
                    and name in sub
                    and sub[name] not in out
                    and not isinstance(sub[name], (dict, list))
                ):
                    out.append(sub[name])
    if typ in DATE_TYPES:
        dates: list[str] = []
        for resp in ctx.responses:
            _dates_in(resp, dates)
        if dates and dates[0] not in out:
            out.append(dates[0])
    return out


def _slug(name: str, ctx: ValueCtx, p: Mapping[str, Any]) -> str:
    s = f"wf{ctx.flow_tag}-{name}"
    lo = p.get("min_length") or 0
    hi = p.get("max_length")
    if hi is not None:
        s = s[: max(int(hi), 0)]
    if len(s) < lo:
        s = s + "x" * (lo - len(s))
    return s


def field_candidates(
    p: Mapping[str, Any], literals: Sequence[str], ctx: ValueCtx, *, key: bool = False
) -> list[tuple[Any, str]]:
    """The values a body field may take, in order, each with its source (D4). A key
    field is always freshly generated."""
    name, typ = p["name"], p.get("type")
    if key:
        return [("{uuid}", "generated")]
    out: list[tuple[Any, str]] = []

    def add(value: Any, source: str) -> None:
        if value is None:
            return
        if all(not (value == v and type(value) is type(v)) for v, _ in out):
            out.append((value, source))

    for c in p.get("choices") or []:
        add(_coerce(str(c), typ), "choices")
    for lit in literals:
        v = _coerce(lit, typ)
        if v is not None and typ in (None, "Str", "Int", "Float", "Bool"):
            add(v, "literal")
    for v in _bound_values(name, typ, ctx):
        add(v, "bound")
    if typ == "Int":
        v = 1
        if p.get("ge") is not None:
            v = max(v, int(p["ge"]))
        if p.get("gt") is not None:
            v = max(v, int(p["gt"]) + 1)
        if p.get("le") is not None:
            v = min(v, int(p["le"]))
        add(v, "default")
    elif typ == "Float":
        add(float(max(1, p.get("ge") or 1)), "default")
    elif typ == "Bool":
        add(False, "default")
        add(True, "default")
    elif typ == "Str":
        add(_slug(name, ctx, p), "default")
    if typ in (None, "Str", "Uuid") or typ in DATE_TYPES:
        for probe in PROBE_STRINGS:
            add(probe, "probe")
    return out


def required_fields(route: RouteV, key_field: str | None) -> list[Mapping[str, Any]]:
    """The body fields a step sends: the required ones and the key field."""
    out = []
    for p in route.params:
        if p["source"] != "body":
            continue
        if p["required"] or p["name"] == key_field:
            out.append(p)
    return sorted(out, key=lambda p: p["name"])


def error_field(body: Any, sent: Iterable[str]) -> str | None:
    """The sent field a 422 answer names (any string value equal to a sent key)."""
    keys = set(sent)
    found: list[str] = []

    def walk(o: Any) -> None:
        if isinstance(o, str):
            if o in keys:
                found.append(o)
        elif isinstance(o, Mapping):
            for k in sorted(o):
                walk(o[k])
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(body)
    return found[0] if found else None


def refusal_code(body: Any) -> str | None:
    """The code of an error answer (``error.code``, ``code``, a string ``error``)."""
    if isinstance(body, Mapping):
        err = body.get("error")
        if isinstance(err, Mapping) and isinstance(err.get("code"), str):
            return err["code"]
        if isinstance(body.get("code"), str):
            return body["code"]
        if isinstance(err, str):
            return err
        if isinstance(body.get("detail"), str):
            return body["detail"]
    return None


def extract_id(body: Any) -> Any:
    """``id`` of an answer: top level, else the one key whose object value has an ``id``."""
    if not isinstance(body, Mapping):
        return None
    if isinstance(body.get("id"), (str, int)) and not isinstance(body.get("id"), bool):
        return body["id"]
    holders = [k for k in sorted(body) if isinstance(body[k], Mapping) and "id" in body[k]]
    if len(holders) == 1 and isinstance(body[holders[0]]["id"], (str, int)):
        return body[holders[0]]["id"]
    return None


def list_id(body: Any) -> Any:
    """The first element's id of the first array-valued top-level key (in the answer's own
    order) whose elements have one; a bare array too."""
    if isinstance(body, list):
        for el in body:
            if isinstance(el, Mapping) and "id" in el:
                return el["id"]
        return None
    if isinstance(body, Mapping):
        for k in body:
            v = body[k]
            if isinstance(v, list) and v and all(isinstance(e, Mapping) and "id" in e for e in v):
                return v[0]["id"]
    return None


# ---- D9: the output -----------------------------------------------------------------------


def _sorted_dict(o: Any) -> Any:
    if isinstance(o, Mapping):
        return {k: _sorted_dict(o[k]) for k in sorted(o)}
    if isinstance(o, (list, tuple)):
        return [_sorted_dict(v) for v in o]
    return o


def to_json(doc: Mapping[str, Any]) -> str:
    """``workflows.json``: sorted keys, two-space indent, one trailing newline."""
    return json.dumps(_sorted_dict(doc), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def summary(flows: Sequence[Mapping[str, Any]], edges: int, machines: int) -> dict[str, int]:
    count = {"verified": 0, "unverified": 0, "failed": 0}
    for f in flows:
        count[f["status"]] = count.get(f["status"], 0) + 1
    return {"flows": len(flows), **count, "edges": edges, "machines": machines}


# ---- rpc flows (Spike I, D6; round 2: D2-D4) ----------------------------------------------
#
# A gRPC rpc is a request step. The dependency edge of the HTTP layer holds unchanged: a
# response field ``id`` (or ``<x>_id``) -- at the top of the response message, or in the one
# message field that carries an ``id`` -- of an rpc that creates a row feeds a later request
# field of the same name, or ``<table>_id``, of an rpc that reads or writes that table (F1).
# "Creates" is evidence: a GORM ``Create`` in the rpc's closure (``grpc.rpcs[].writes`` of
# the extract). Round 2 adds three things. F2: a request string field ``<s>_<col>`` that
# names a row of a table the rpc uses by a natural key (``queue_slug`` of ``queues``) is bound
# from an earlier step's response field ``col`` or from a *lookup step* inserted before the
# step that needs it (a flow with no source is ``unbound``, never run). F3: the example value
# of a field nothing binds is the least privileged one (``bool`` false, an enum's first
# non-zero value). F4: an ``owner_chain`` plan -- another user attaching a child row to a
# parent row of the first user -- is refused. Flows are lists of rpc steps with the callers (A
# and B: the spec's first two users) and the values bound from earlier responses; each is run
# on a throwaway SQLite file against the app's own binary, recorded as concrete ``Rpc`` steps
# with the binding of every field (F6), and *verified* only when every step answered as the plan
# means (a success where it expects one, a refusal where it expects one, never INTERNAL or
# UNKNOWN; guard refusals only NOT_FOUND or PERMISSION_DENIED) and the recorded scenario passes
# a replay of layer D on a fresh database.

RPC_FLOWS_SCHEMA = "lattice.rpc-flows/2"
RPC_REFUSALS = frozenset(
    {
        "NOT_FOUND",
        "PERMISSION_DENIED",
        "UNAUTHENTICATED",
        "FAILED_PRECONDITION",
        "INVALID_ARGUMENT",
        "ALREADY_EXISTS",
        "RESOURCE_EXHAUSTED",
        "OUT_OF_RANGE",
        "ABORTED",
    }
)
RPC_FAULTS = frozenset({"INTERNAL", "UNKNOWN", "DATA_LOSS", "UNAVAILABLE", "DEADLINE_EXCEEDED"})
GUARD_KINDS = frozenset({"owner_guard", "owner_chain"})
GUARD_REFUSALS = frozenset({"NOT_FOUND", "PERMISSION_DENIED"})
LIFECYCLE_MAX = 4


@dataclass(frozen=True)
class RpcView:
    """One implemented rpc of the extraction, as the flow layer reads it."""

    key: str  # "notes.v1.Notes/GetNote"
    service: str
    method: str
    inputs: tuple[Mapping[str, Any], ...]  # the request message's fields
    id_inputs: tuple[str, ...]  # request fields named id or <x>_id
    makes_id: bool  # the response carries an id (top level, or in its one message field)
    auth: str
    tables: tuple[str, ...]  # SQL names
    creates: tuple[str, ...]
    deletes: tuple[str, ...]
    writes: tuple[str, ...]  # tables written other than by create
    owner_compare: bool
    # response fields a natural key can be read from: top-level and one message deep
    out_paths: tuple[tuple[str, ...], ...] = ()
    # repeated message fields of the response: (field name, the element message's field names)
    out_lists: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def public(self) -> bool:
        return self.auth == "Public"


def _is_id_name(name: str) -> bool:
    return name == "id" or name.endswith("_id")


def rpc_views(extract: Mapping[str, Any]) -> list[RpcView]:
    """The implemented unary rpcs of ``extract["grpc"]``, in the contract's order."""
    grpc = extract.get("grpc") or {}
    messages = {m["name"]: m for m in grpc.get("messages", [])}
    out: list[RpcView] = []
    for svc in grpc.get("services", []):
        for r in svc["rpcs"]:
            if r.get("state") != "implemented":
                continue
            fields = tuple(messages.get(r["input"], {}).get("fields", ()))
            resp = messages.get(r["output"], {}).get("fields", ())
            holders = [
                f
                for f in resp
                if f["label"] == "single"
                and f["type"] in messages
                and any(x["name"] == "id" for x in messages[f["type"]]["fields"])
            ]
            makes = any(_is_id_name(f["name"]) and f["type"] != "map" for f in resp) or (
                len(holders) == 1
            )
            paths: list[tuple[str, ...]] = []
            lists: list[tuple[str, tuple[str, ...]]] = []
            for f in resp:
                if f["label"] == "single" and f["type"] not in messages and f["type"] != "map":
                    paths.append((f["name"],))
                elif f["label"] == "single" and f["type"] in messages:
                    for g in messages[f["type"]]["fields"]:
                        if g["label"] == "single" and g["type"] not in messages:
                            paths.append((f["name"], g["name"]))
                elif f["label"] == "repeated" and f["type"] in messages:
                    lists.append(
                        (f["name"], tuple(g["name"] for g in messages[f["type"]]["fields"]))
                    )
            ws = r.get("writes") or []
            out.append(
                RpcView(
                    key=f"{svc['name']}/{r['name']}",
                    service=svc["name"],
                    method=r["name"],
                    inputs=fields,
                    id_inputs=tuple(f["name"] for f in fields if _is_id_name(f["name"])),
                    makes_id=makes,
                    auth=r.get("auth", "unclear"),
                    tables=tuple(r.get("tables") or ()),
                    creates=tuple(dict.fromkeys(w["table"] for w in ws if w["op"] == "create")),
                    deletes=tuple(dict.fromkeys(w["table"] for w in ws if w["op"] == "delete")),
                    writes=tuple(
                        dict.fromkeys(w["table"] for w in ws if w["op"] in ("save", "update"))
                    ),
                    owner_compare=any(o["how"] == "compare" for o in r.get("owner_uses") or []),
                    out_paths=tuple(paths),
                    out_lists=tuple(lists),
                )
            )
    return out


def rpc_enums(extract: Mapping[str, Any]) -> dict[str, tuple[tuple[str, int], ...]]:
    """``{enum full name: ((value name, number), ...)}`` of ``extract["grpc"]``."""
    grpc = extract.get("grpc") or {}
    return {
        e["name"]: tuple((v["name"], int(v["number"])) for v in e.get("values", ()))
        for e in grpc.get("enums", [])
    }


def _singular(table: str) -> str:
    return table[:-1] if table.endswith("s") and len(table) > 1 else table


def rpc_edges(views: Sequence[RpcView]) -> list[tuple[str, str, str, str]]:
    """``(table, producer key, consumer key, request field)``: the producer creates the
    table's row and answers an id; the consumer takes it as ``id`` or ``<table>_id`` (F1)."""
    out = []
    for p in views:
        if not p.makes_id:
            continue
        for t in p.creates:
            for c in views:
                if c.key == p.key or t not in c.tables:
                    continue
                for f in c.id_inputs:
                    if f == "id" or f == f"{_singular(t)}_id" or f == f"{t}_id":
                        out.append((t, p.key, c.key, f))
                        break
    return out


def example_value(
    f: Mapping[str, Any], enums: Mapping[str, Sequence[tuple[str, int]]] | None = None
) -> Any:
    """A value a request field accepts, from its type and bounds (None: leave it out). F3: the
    least privileged one -- ``bool`` false, an enum's first value other than number 0 (proto3's
    zero is ``*_UNSPECIFIED``); ``enums`` maps an enum's full name to its values."""
    b = f.get("bounds") or {}
    if f["label"] != "single":
        return None
    if "choices" in b and b["choices"]:
        return b["choices"][0]
    t = f["type"]
    if t in ("string", "bytes"):
        lo, hi = int(b.get("min_length") or 0), b.get("max_length")
        text = f"wf-{f['name']}"
        text = text.ljust(max(lo, 1), "x")
        return text[: int(hi)] if hi is not None else text
    if t == "bool":
        return False
    if t in ("double", "float"):
        return b.get("ge", b["gt"] + 1 if "gt" in b else 0.0)
    if t.endswith("int32") or t.endswith("int64") or t in ("fixed32", "fixed64"):
        if "ge" in b:
            return b["ge"]
        return b["gt"] + 1 if "gt" in b else 0
    if enums and t in enums:
        for name, number in enums[t]:
            if number != 0:
                return name
    return None  # a message, or an enum that has only its zero value: left out


def rpc_message(
    view: RpcView,
    bound: Mapping[str, Any],
    enums: Mapping[str, Sequence[tuple[str, int]]] | None = None,
) -> dict[str, Any]:
    """The request of a step: the values bound from earlier responses, and -- for an rpc that
    writes -- an example of every scalar field."""
    msg: dict[str, Any] = {}
    writes = bool(view.creates or view.writes or view.deletes)
    for f in view.inputs:
        if f["name"] in bound:
            msg[f["name"]] = bound[f["name"]]
        elif writes:
            v = example_value(f, enums)
            if v is not None:
                msg[f["name"]] = v
    return msg


def natural_fields(view: RpcView) -> list[tuple[str, str, str]]:
    """``(request field, table, col)``: a request string field ``<s>_<col>``, ``col`` not ``id``,
    where ``s`` is the singular of a table the rpc uses (F2). A field of a oneof is not one."""
    out = []
    for f in view.inputs:
        name = f["name"]
        if f["label"] != "single" or f["type"] != "string" or f.get("oneof"):
            continue
        if _is_id_name(name) or "_" not in name:
            continue
        hit = None
        for i, ch in enumerate(name):
            if ch != "_" or i == 0 or i == len(name) - 1:
                continue
            s, col = name[:i], name[i + 1 :]
            for t in sorted(view.tables):
                if _singular(t) == s:
                    hit = (name, t, col)
                    break
            if hit:
                break
        if hit:
            out.append(hit)
    return out


def _provides(view: RpcView, table: str, col: str) -> bool:
    """Whether ``view`` reads or creates ``table`` and answers a field ``col``."""
    if table not in view.tables:
        return False
    return any(p[-1] == col for p in view.out_paths) or any(col in el for _, el in view.out_lists)


def _lookup_for(views: Sequence[RpcView], table: str, col: str) -> RpcView | None:
    """F2(b): the first implemented rpc in sorted key order that reads ``table``, has no id-like
    input and answers a repeated message field whose elements have ``col``."""
    for v in sorted(views, key=lambda v: v.key):
        if table in v.tables and not v.id_inputs and any(col in el for _, el in v.out_lists):
            return v
    return None


@dataclass
class RpcPlanStep:
    key: str
    caller: str | None  # "A", "B" or None
    binds: dict[str, int]  # request field -> the 1-based step whose response id feeds it
    intent: str = "ok"  # ok | refuse | any
    same_as: int | None = None  # the 1-based step whose request fields this one repeats
    role: str = "step"  # step | lookup
    nat: dict[str, tuple[int, str]] = field(default_factory=dict)  # field -> (step, col)


@dataclass
class RpcPlan:
    kind: str
    steps: list[RpcPlanStep]
    why: str
    unbound: str | None = None  # "no_source:<field>": the plan cannot be run

    @property
    def id(self) -> str:
        parts = []
        for s in self.steps:
            part = f"{s.key}@{s.caller}:{sorted(s.binds.items())}:{s.intent}"
            if s.role != "step":
                part += f":{s.role}"
            if s.nat:
                part += f":{sorted(s.nat.items())}"
            parts.append(part)
        raw = self.kind + "|" + "|".join(parts)
        if self.unbound:
            raw += "|" + self.unbound
        return "rf-" + hashlib.sha256(raw.encode()).hexdigest()[:10]


def _caller(view: RpcView, who: str) -> str | None:
    return None if view.public else who


def expand_natural(plan: RpcPlan, views: Sequence[RpcView]) -> RpcPlan:
    """F2: bind every natural key of ``plan``'s steps -- from an earlier step that answers the
    column, else from a lookup step inserted before the step that needs it -- or mark the plan
    ``unbound``. Idempotent: a field already bound is left alone."""
    by_key = {v.key: v for v in views}
    steps = list(plan.steps)
    i = 0
    while i < len(steps):
        st = steps[i]
        for fld, table, col in natural_fields(by_key[st.key]):
            if fld in st.nat or fld in st.binds:
                continue
            src = None
            for j in range(i):
                prev = steps[j]
                if prev.intent == "ok" and _provides(by_key[prev.key], table, col):
                    src = j + 1
            if src is None:
                look = _lookup_for(views, table, col)
                if look is None:
                    return RpcPlan(plan.kind, steps, plan.why, unbound=f"no_source:{fld}")
                for s in steps:  # the lookup takes 1-based position i + 1
                    s.binds = {f: (k + 1 if k >= i + 1 else k) for f, k in s.binds.items()}
                    s.nat = {f: ((k + 1 if k >= i + 1 else k), c) for f, (k, c) in s.nat.items()}
                    if s.same_as is not None and s.same_as >= i + 1:
                        s.same_as += 1
                steps.insert(i, RpcPlanStep(look.key, _caller(look, "A"), {}, role="lookup"))
                i += 1
                st = steps[i]
                src = i
            st.nat[fld] = (src, col)
        i += 1
    return RpcPlan(plan.kind, steps, plan.why, unbound=plan.unbound)


def natural_value(body: Any, col: str, *, lists_first: bool = False) -> Any:
    """The value of the response field ``col``: a top-level or one-message-deep scalar, or the
    smallest ``col`` among the elements of a repeated message field (sorted, so the choice is
    deterministic); None when the body has none."""

    def scalar() -> Any:
        if not isinstance(body, Mapping):
            return None
        if isinstance(body.get(col), (str, int)) and not isinstance(body.get(col), bool):
            return body[col]
        for k in sorted(body):
            sub = body[k]
            if isinstance(sub, Mapping) and isinstance(sub.get(col), (str, int)):
                return sub[col]
        return None

    def smallest() -> Any:
        if not isinstance(body, Mapping):
            return None
        for k in sorted(body):
            seq = body[k]
            if isinstance(seq, list):
                vals = [
                    e[col] for e in seq if isinstance(e, Mapping) and isinstance(e.get(col), str)
                ]
                if vals:
                    return min(vals)
        return None

    for pick in (smallest, scalar) if lists_first else (scalar, smallest):
        got = pick()
        if got is not None:
            return got
    return None


def id_source(body: Any) -> tuple[Any, str] | None:
    """``(id, the response field it came from)`` of an answer, as :func:`extract_id` reads it."""
    if not isinstance(body, Mapping):
        return None
    if isinstance(body.get("id"), (str, int)) and not isinstance(body.get("id"), bool):
        return body["id"], "id"
    holders = [k for k in sorted(body) if isinstance(body[k], Mapping) and "id" in body[k]]
    if len(holders) == 1 and isinstance(body[holders[0]]["id"], (str, int)):
        return body[holders[0]]["id"], f"{holders[0]}.id"
    return None


def static_rpc_plans(views: Sequence[RpcView]) -> list[RpcPlan]:
    """The flows the contract and the closures support, before anything runs: ``pair``
    (one per edge), ``owner_guard`` (the consumer as another user), ``owner_chain`` (another user
    attaching a child row to the first user's parent row, F4), ``after_delete`` (a read after the
    row is deleted) and ``account`` (a public rpc that creates users, then the public rpc that
    takes the same fields). Natural keys are bound or the plan is ``unbound`` (F2)."""
    by_key = {v.key: v for v in views}
    plans: list[RpcPlan] = []
    for table, p, c, field_ in rpc_edges(views):
        pv, cv = by_key[p], by_key[c]
        plans.append(
            RpcPlan(
                "pair",
                [
                    RpcPlanStep(p, _caller(pv, "A"), {}),
                    RpcPlanStep(c, _caller(cv, "A"), {field_: 1}),
                ],
                f"{p} creates a {table} row; {c} takes its id as {field_}",
            )
        )
        if cv.owner_compare and not cv.public:
            plans.append(
                RpcPlan(
                    "owner_guard",
                    [
                        RpcPlanStep(p, _caller(pv, "A"), {}),
                        RpcPlanStep(c, "B", {field_: 1}, "refuse"),
                    ],
                    f"{c} compares the owner: another user must be refused",
                )
            )
        if field_ != "id" and cv.creates and not (set(cv.creates) <= {table}) and not cv.public:
            plans.append(
                RpcPlan(
                    "owner_chain",
                    [
                        RpcPlanStep(p, _caller(pv, "A"), {}),
                        RpcPlanStep(c, "B", {field_: 1}, "refuse"),
                    ],
                    f"{c} adds a {', '.join(cv.creates)} row under a {table} row it does not own:"
                    " another user must be refused",
                )
            )
        if cv.deletes:
            for r in views:
                if r.key == c or table not in r.tables or r.deletes or r.creates or r.writes:
                    continue
                if r.id_inputs:
                    # F1: only a field that names this table's row takes its id
                    named = [
                        f
                        for f in r.id_inputs
                        if f in ("id", f"{_singular(table)}_id", f"{table}_id")
                    ]
                    if not named:
                        continue
                    f2 = field_ if field_ in named else named[0]
                    plans.append(
                        RpcPlan(
                            "after_delete",
                            [
                                RpcPlanStep(p, _caller(pv, "A"), {}),
                                RpcPlanStep(c, _caller(cv, "A"), {field_: 1}),
                                RpcPlanStep(r.key, _caller(r, "A"), {f2: 1}, "refuse"),
                            ],
                            f"{r.key} reads the {table} row after {c} deleted it",
                        )
                    )
                else:
                    plans.append(
                        RpcPlan(
                            "after_delete",
                            [
                                RpcPlanStep(p, _caller(pv, "A"), {}),
                                RpcPlanStep(c, _caller(cv, "A"), {field_: 1}),
                                RpcPlanStep(r.key, _caller(r, "A"), {}),
                            ],
                            f"{r.key} lists the {table} rows after {c} deleted one",
                        )
                    )
    # account: a public rpc creating the users table, and a public rpc over it whose fields
    # the creator's request also has
    for r in views:
        if not (r.public and r.creates and not r.id_inputs):
            continue
        names = {f["name"] for f in r.inputs}
        for login in views:
            if login.key == r.key or not login.public or login.creates or login.writes:
                continue
            if set(r.creates) & set(login.tables) and {f["name"] for f in login.inputs} <= names:
                plans.append(
                    RpcPlan(
                        "account",
                        [RpcPlanStep(r.key, None, {}), RpcPlanStep(login.key, None, {}, same_as=1)],
                        f"{login.key} takes the credentials {r.key} created",
                    )
                )
    seen: set[str] = set()
    out = []
    for p in plans:
        p = expand_natural(p, views)
        if p.id not in seen:
            seen.add(p.id)
            out.append(p)
    return out


def rpc_lifecycle_creators(views: Sequence[RpcView]) -> list[tuple[str, RpcView, list[RpcView]]]:
    """``(table, creator, writers)``: the rpcs that update a created row by its id."""
    out = []
    edges = rpc_edges(views)
    by_key = {v.key: v for v in views}
    for table in dict.fromkeys(e[0] for e in edges):
        creators = [by_key[p] for t, p, _, _ in edges if t == table]
        writers = [
            by_key[c]
            for t, _, c, _ in edges
            if t == table and (by_key[c].writes) and not by_key[c].deletes
        ]
        if creators and len(writers) >= 2:
            out.append((table, creators[0], list({w.key: w for w in writers}.values())))
    return out


def rpc_step_ok(kind: str, intent: str, code: str) -> bool:
    """Whether a step answered as its plan means (D3): never a fault; ``OK`` where the plan
    expects a success; a refusal where it expects one -- for the guard kinds only NOT_FOUND or
    PERMISSION_DENIED (a missing or wrong session is not an ownership refusal)."""
    if code in RPC_FAULTS:
        return False
    if intent == "ok":
        return code == "OK"
    if intent == "refuse":
        return code in (GUARD_REFUSALS if kind in GUARD_KINDS else RPC_REFUSALS)
    return True


def rpc_flows_json(flows: Sequence[Mapping[str, Any]]) -> str:
    """``rpc-flows.json``: sorted keys, two-space indent, one trailing newline."""
    count = {"verified": 0, "unverified": 0, "failed": 0, "unbound": 0}
    for f in flows:
        count[f["status"]] = count.get(f["status"], 0) + 1
    doc = {
        "schema": RPC_FLOWS_SCHEMA,
        "flows": list(flows),
        "summary": {"flows": len(flows), **count, "edges": 0, "machines": 0},
    }
    return to_json(doc)
