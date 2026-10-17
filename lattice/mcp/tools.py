"""The tools of the Lattice MCP server, over a project.

A tool is ``name, description, inputSchema, handler``; a handler takes the arguments dict
and returns a JSON-able dict, or raises :class:`ToolError` (answered as ``isError``).

Two backends supply the model, the LLM-backed answers, change sets and checks:

- :class:`LocalBackend` runs ``lattice.studio.server.Studio`` in this process (its own
  change records; the LLM is ``ClaudeCliClient`` unless a generator is injected);
- :class:`HttpBackend` calls a running Studio's HTTP API (``--studio-url``), so a change
  set proposed here shows up in Studio's UI where the owner accepts it.

Nothing here accepts, rejects or deletes a change set or writes a project file.
"""

from __future__ import annotations

import json
import os
import re
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

OUTPUT_CAP = 20_000
DEFAULT_CHECK_TIMEOUT = 600.0
PROJECT_FILES = ("REQUIREMENTS.md", "spec.py", "spec.trace.json")
SECTIONS = ("rules", "views", "data_model", "app", "changes")


class ToolError(Exception):
    """A tool failure the model should see (answered as ``isError: true``)."""


# ---- backends ---------------------------------------------------------------------------


class LocalBackend:
    """The Studio engine in this process."""

    kind = "local"

    def __init__(self, root: Path, *, model: str | None = None, generator: Any = None) -> None:
        from lattice.studio.server import Studio

        self.root = root.resolve()
        self.studio = Studio(self.root, model=model, generator=generator)

    @property
    def configured(self) -> bool:
        return self.studio.harness is not None

    def _call(self, fn: Callable[..., Any], *a: Any) -> Any:
        from lattice.studio.server import ApiError

        try:
            return fn(*a)
        except ApiError as e:
            raise ToolError(e.detail) from None

    def model(self) -> dict[str, Any]:
        return self._call(self.studio.get_model)

    def lint(self) -> list[dict[str, Any]]:
        return self._call(self.studio._base_lint)

    def ask(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(self.studio.ask, body)

    def new_change(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self._call(self.studio.new_change, body)

    def get_change(self, cid: str) -> dict[str, Any]:
        return self._call(self.studio.get_change, cid)

    def check(self, layers: str | None, timeout: float) -> dict[str, Any]:
        st = self.studio
        if st.harness is None:
            raise ToolError("no app is configured (studio.toml [app] app and db)")
        old = st.config.check.get("layers")
        if layers:
            st.config.check["layers"] = layers
        try:
            started = st.start_check()
            deadline = time.monotonic() + timeout
            while True:
                with st.lock:
                    if not st.check_running:
                        out = st.last_check
                        break
                if time.monotonic() > deadline:
                    raise ToolError(
                        f"the check is still running after {timeout:g}s; call check_run again "
                        "to keep waiting for it"
                    )
                time.sleep(0.2)
        finally:
            if layers:
                if old is None:
                    st.config.check.pop("layers", None)
                else:
                    st.config.check["layers"] = old
        out = dict(out or {})
        out["already_running"] = not started.get("started", False)
        return out


SPEC_FILES = ("REQUIREMENTS.md", "spec.py", "spec.trace.json")


class PendingBackend:
    """A project that has no Lattice spec yet (a new project, or any other directory).

    The server still starts, so a user-level plugin does not show a failed server in every
    directory. Each call re-checks the project: the moment REQUIREMENTS.md, spec.py and
    spec.trace.json exist (``lattice spec draft``), calls are served by the real engine
    without restarting the session. Until then every tool answers with what is missing.
    """

    def __init__(self, root: Path, *, model: str | None = None) -> None:
        self.root = root.resolve()
        self._model = model
        self._real: LocalBackend | None = None

    @property
    def kind(self) -> str:
        return "local" if self._real is not None else "pending"

    def missing(self) -> list[str]:
        return [n for n in SPEC_FILES if not (self.root / n).is_file()]

    def _resolve(self) -> LocalBackend:
        if self._real is None:
            miss = self.missing()
            if miss:
                raise ToolError(
                    f"{self.root} is not a Lattice project yet (missing: {', '.join(miss)}). "
                    "Write REQUIREMENTS.md (the rules in plain language), then run "
                    "`lattice spec draft` in this directory to draft spec.py and spec.trace.json; "
                    "after that these tools work without restarting." + self._consumer_note()
                )
            self._real = LocalBackend(self.root, model=self._model)
        return self._real

    def _consumer_note(self) -> str:
        """One sentence for a contract consumer's directory (it holds an imports file and/or a
        lock): its contracts need no spec. Empty for every other directory."""
        from lattice.contracts.check import IMPORTS_FILE, LOCK_FILE

        files = [n for n in (IMPORTS_FILE, LOCK_FILE) if (self.root / n).is_file()]
        if not files:
            return ""
        return (
            f" This directory is a contract consumer (it holds {' and '.join(files)}), which "
            "needs no spec for its contracts: with a workspace, contract_check and "
            "linked_question_list show what its providers changed and the notes they left "
            "(otherwise `lattice contracts check` and `lattice contracts questions list` with "
            "--workspace)."
        )

    @property
    def configured(self) -> bool:
        return self._real is not None and self._real.configured

    def model(self) -> dict[str, Any]:
        return self._resolve().model()

    def lint(self) -> list[dict[str, Any]]:
        return self._resolve().lint()

    def ask(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self._resolve().ask(body)

    def new_change(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self._resolve().new_change(body)

    def get_change(self, cid: str) -> dict[str, Any]:
        return self._resolve().get_change(cid)

    def check(self, layers: str | None, timeout: float) -> dict[str, Any]:
        return self._resolve().check(layers, timeout)


class HttpBackend:
    """A running Studio, over its JSON API."""

    kind = "http"

    def __init__(self, url: str, root: Path) -> None:
        self.url = url.rstrip("/")
        self.root = root.resolve()
        self.configured = bool(self.model().get("app", {}).get("configured"))

    def _req(self, method: str, path: str, body: Any = None, timeout: float = 900.0) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.url + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 -- local URL
                return json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode()).get("detail") or e.reason
            except Exception:  # noqa: BLE001
                detail = e.reason
            raise ToolError(f"Studio answered {e.code}: {detail}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ToolError(f"cannot reach Studio at {self.url}: {e}") from None

    def model(self) -> dict[str, Any]:
        return self._req("GET", "/api/model")

    def lint(self) -> list[dict[str, Any]]:
        return list(self.model().get("views", {}).get("lint_gaps") or [])

    def ask(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self._req("POST", "/api/ask", dict(body))

    def new_change(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self._req("POST", "/api/changes", dict(body))

    def get_change(self, cid: str) -> dict[str, Any]:
        return self._req("GET", "/api/changes/" + urllib.request.quote(cid, safe=""))

    def check(self, layers: str | None, timeout: float) -> dict[str, Any]:
        if not self.configured:
            raise ToolError("no app is configured (studio.toml [app] app and db)")
        started = self._req("POST", "/api/check", {})
        deadline = time.monotonic() + timeout
        time.sleep(0.3)
        while True:
            app = self.model().get("app", {})
            if not app.get("check_running"):
                out = dict(app.get("last_check") or {})
                out["already_running"] = not started.get("started", False)
                return out
            if time.monotonic() > deadline:
                raise ToolError(
                    f"the check is still running after {timeout:g}s; call check_run again "
                    "to keep waiting for it"
                )
            time.sleep(1.0)


# ---- derived data: targets, routes, graph, code links ------------------------------------

_ROUTE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s`'\"),;]*)")
_SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    "vendor",
    "evidence",
    ".mypy_cache",
    ".pytest_cache",
}
_CODE_EXT = {".py", ".go", ".ts", ".tsx", ".js", ".jsx", ".java", ".rb", ".rs", ".kt", ".cs"}


def norm_route(method: str, path: str) -> str:
    """``POST /applications/1/runs/{id}/cancel`` -> ``POST /applications/{}/runs/{}/cancel``."""
    path = path.split("?", 1)[0].rstrip("/") or "/"
    segs = []
    for s in path.split("/"):
        if s.isdigit() or (s.startswith("{") and s.endswith("}")) or s.startswith(":"):
            segs.append("{}")
        else:
            segs.append(s)
    return f"{method.upper()} " + "/".join(segs)


def routes_in(text: str) -> list[str]:
    return [norm_route(m, p) for m, p in _ROUTE.findall(text)]


def canon_target(t: str) -> str:
    t = t.strip()
    return f"rule:{t}" if re.fullmatch(r"R\d+", t) else t


class ModelView:
    """Lookups over a model dict (the GET /api/model shape)."""

    def __init__(self, model: Mapping[str, Any]) -> None:
        self.model = model
        self.rules = {r["id"]: r for r in model.get("rules", [])}
        s6 = (model.get("views") or {}).get("section6") or {}
        self.scenarios = {
            x["target"]: x for x in s6.get("scenarios") or [] if isinstance(x, Mapping)
        }
        self.invariants = {x[0]: x for x in s6.get("invariants") or []}
        self.machines = {x[0]: x for x in s6.get("machines") or []}
        self.services = {x[0]: x for x in s6.get("services") or []}
        self.endpoints: list[str] = []
        for g in (model.get("views") or {}).get("access_groups") or []:
            for c in g.get("columns") or []:
                if c not in self.endpoints:
                    self.endpoints.append(c)
        self.tables = {t["name"]: t for t in (model.get("data_model") or {}).get("tables", [])}
        self.questions = {q["id"]: q for q in (model.get("views") or {}).get("questions") or []}

    def routes_of(self, target: str) -> list[str]:
        """The endpoints a target exercises: cell column, scenario steps, rule links+text."""
        target = canon_target(target)
        kind, _, rest = target.partition(":")
        out: list[str] = []
        if kind == "cell":
            col = rest.split("|", 1)[-1]
            out = routes_in(col)
        elif kind == "endpoint":
            out = routes_in(rest)
        elif kind == "scenario":
            sc = self.scenarios.get(target)
            out = routes_in(" ".join(sc.get("steps") or [])) if sc else []
        elif kind == "rule":
            r = self.rules.get(rest)
            if r:
                out = routes_in(r.get("text", ""))
                for link in r.get("links", []):
                    out += self.routes_of(link)
        elif re.fullmatch(r"[A-Z]+ /\S*", target):
            out = [norm_route(*target.split(" ", 1))]
        seen: dict[str, None] = {}
        for x in out:
            seen.setdefault(x)
        return list(seen)

    def known(self, target: str) -> bool:
        target = canon_target(target)
        kind, _, rest = target.partition(":")
        if kind == "rule":
            return rest in self.rules
        if kind == "scenario":
            return target in self.scenarios
        if kind == "invariant":
            return target in self.invariants
        if kind == "machine":
            return target in self.machines
        if kind == "service":
            return target in self.services
        if kind == "profile":
            return rest.split(".", 1)[0] in self.tables
        if kind in ("cell", "endpoint"):
            return bool(self.routes_of(target))
        return bool(re.fullmatch(r"[A-Z]+ /\S*", target))


def _route_regex(route: str, k: int) -> re.Pattern[str] | None:
    segs = [s for s in route.split(" ", 1)[1].split("/") if s != ""]
    tail = segs[-k:]
    if len(tail) < min(k, 2) or not any(s != "{}" for s in tail):
        return None
    ph = r"(?:\{[^}/]*\}|:[A-Za-z_]\w*|<[^>/]*>|%[sdv]|\*)"
    body = "/".join(ph if s == "{}" else re.escape(s) for s in tail)
    lead = r"/?" if k == len(segs) else r"/?"
    return re.compile(r"""(?:["'`]|/)""" + lead + body + r"""/?(?:["'`?)]|$)""")


def _code_files(roots: Sequence[Path], skip: set[str], limit: int = 3000) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        for dp, dns, fns in os.walk(root):
            dns[:] = sorted(d for d in dns if d not in _SKIP_DIRS and not d.startswith("."))
            for fn in sorted(fns):
                p = Path(dp) / fn
                if p.suffix in _CODE_EXT and fn not in skip:
                    try:
                        if p.stat().st_size <= 400_000:
                            out.append(p)
                    except OSError:
                        continue
                if len(out) >= limit:
                    return out
    return out


def source_roots(root: Path) -> list[Path]:
    """The project directory plus the code dirs its studio.toml names."""
    roots = [root]
    try:
        cfg = tomllib.loads((root / "studio.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        cfg = {}
    cands: list[str] = []
    for sect, keys in (
        ("check", ("go_src", "app_dir", "src", "source", "src_dir")),
        ("app", ("cwd",)),
    ):
        for k in keys:
            v = (cfg.get(sect) or {}).get(k)
            if isinstance(v, str):
                cands.append(v)
    for c in cands:
        p = (root / c).resolve()
        if p.is_dir() and p not in roots:
            roots.append(p)
    return roots


def grep_routes(
    root: Path, routes: Sequence[str], *, per_route: int = 5, total: int = 40
) -> list[dict[str, Any]]:
    """Lines of code naming each route: the longest tail of its path (>= 2 segments, or the
    whole path) as a quoted literal, with placeholders matched loosely."""
    files = _code_files(source_roots(root), set(PROJECT_FILES))
    out: list[dict[str, Any]] = []
    texts: dict[Path, list[str]] = {}
    for route in routes:
        n = len([s for s in route.split(" ", 1)[1].split("/") if s])
        hits = 0
        pats = [(k, _route_regex(route, k)) for k in range(n, 0, -1)]
        pats = [(k, p) for k, p in pats if p is not None and (k >= 2 or n == 1)]
        method = route.split(" ", 1)[0]
        for f in files:
            if hits >= per_route or len(out) >= total:
                break
            lines = texts.get(f)
            if lines is None:
                try:
                    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    lines = []
                texts[f] = lines
            for i, line in enumerate(lines, 1):
                for k, pat in pats:
                    if pat.search(line):
                        sym = re.search(
                            r",\s*([A-Za-z_][\w.]*)\s*\)", line[pat.search(line).end() :]
                        )
                        try:
                            rel = str(f.relative_to(root))
                        except ValueError:
                            rel = str(f)
                        out.append(
                            {
                                "path": rel,
                                "line_from": i,
                                "line_to": i,
                                "symbol": sym.group(1) if sym else None,
                                "why": f"{route}: route literal ({k} of {n} segments)"
                                + (" with method" if method.lower() in line.lower() else ""),
                            }
                        )
                        hits += 1
                        break
                if hits >= per_route:
                    break
    return out


def code_links(root: Path, view: ModelView, target: str) -> dict[str, Any]:
    target = canon_target(target)
    if not view.known(target):
        raise ToolError(f"unknown target {target!r}")
    kind, _, rest = target.partition(":")
    routes = view.routes_of(target)[:15]
    links: list[dict[str, Any]] = []
    if routes:
        links = grep_routes(root, routes)
    elif kind == "profile":
        sql = view.tables[rest.split(".", 1)[0]].get("sql") or rest
        pat = re.compile(rf"\b{re.escape(sql)}\b")
        for f in _code_files(source_roots(root), set(PROJECT_FILES)):
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if pat.search(line):
                    rel = str(f.relative_to(root)) if f.is_relative_to(root) else str(f)
                    links.append(
                        {
                            "path": rel,
                            "line_from": i,
                            "line_to": i,
                            "symbol": None,
                            "why": f"table name {sql}",
                        }
                    )
                    break
            if len(links) >= 20:
                break
    note = (
        "derived from the spec's routes and a text search of the source; no code index "
        "is built by the real Studio, so treat these as leads, not proof"
    )
    return {
        "target": target,
        "source": "trace+grep",
        "routes": routes,
        "links": links,
        "note": note,
    }


def build_graph(view: ModelView) -> dict[str, Any]:
    """Rules, endpoints, tables, machines, services, invariants and (as leaves) scenarios,
    joined by the trace's links and the routes scenarios call."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, str]] = {}

    def node(kind: str, target: str, label: str, sub: str | None = None) -> None:
        nodes.setdefault(
            target, {"id": target, "kind": kind, "label": label, **({"sub": sub} if sub else {})}
        )

    def edge(src: str, dst: str, kind: str) -> None:
        if src in nodes and dst in nodes:
            edges.setdefault(f"{src}>{kind}>{dst}", {"source": src, "target": dst, "kind": kind})

    for rid, r in view.rules.items():
        node("rule", f"rule:{rid}", rid, r["text"][:140])
    for e in view.endpoints:
        node("endpoint", f"endpoint:{e}", e)
    for n, t in view.tables.items():
        node("table", f"profile:{n}", n, t.get("sql"))
    for t, x in view.machines.items():
        node("machine", t, x[1], x[2])
    for t, x in view.services.items():
        node("service", t, x[1], x[2])
    for t, x in view.invariants.items():
        node("invariant", t, x[1], x[2])
    for t, sc in view.scenarios.items():
        node("scenario", t, sc.get("name") or t)
    ep_by_route = {norm_route(*e.split(" ", 1)): f"endpoint:{e}" for e in view.endpoints}
    for t in view.scenarios:
        for rt in view.routes_of(t):
            if rt in ep_by_route:
                edge(t, ep_by_route[rt], "traces")
    for rid, r in view.rules.items():
        src = f"rule:{rid}"
        for link in r.get("links", []):
            kind = link.split(":", 1)[0]
            if kind in ("scenario", "invariant"):
                edge(src, link, "traces")
            elif kind == "machine":
                edge(src, link, "transitions")
            elif kind == "service":
                edge(src, link, "applies_to")
            elif kind == "profile":
                edge(src, "profile:" + link.split(":", 1)[1].split(".", 1)[0], "applies_to")
            elif kind == "cell":
                for rt in view.routes_of(link):
                    if rt in ep_by_route:
                        edge(src, ep_by_route[rt], "enforces")
        for rt in routes_in(r.get("text", "")):
            if rt in ep_by_route:
                edge(src, ep_by_route[rt], "enforces")
    return {"nodes": nodes, "edges": list(edges.values())}


def resolve_focus(view: ModelView, nodes: Mapping[str, Any], focus: str) -> str | None:
    f = canon_target(focus)
    if f in nodes:
        return f
    kind, _, rest = f.partition(":")
    if kind == "profile":
        t = "profile:" + rest.split(".", 1)[0]
        return t if t in nodes else None
    if kind in ("cell", "endpoint") or re.fullmatch(r"[A-Z]+ /\S*", f):
        for rt in view.routes_of(f):
            for e in view.endpoints:
                if norm_route(*e.split(" ", 1)) == rt:
                    return f"endpoint:{e}"
    return None


def graph_get(view: ModelView, focus: str | None, depth: int, limit: int = 150) -> dict[str, Any]:
    g = build_graph(view)
    nodes, edges = g["nodes"], g["edges"]
    total = {"nodes": len(nodes), "edges": len(edges)}
    if focus:
        start = resolve_focus(view, nodes, focus)
        if start is None:
            raise ToolError(f"no graph node matches {focus!r}")
        adj: dict[str, list[str]] = {}
        for e in edges:
            adj.setdefault(e["source"], []).append(e["target"])
            adj.setdefault(e["target"], []).append(e["source"])
        order = [start]
        seen = {start}
        frontier = [start]
        for _ in range(depth):
            nxt = []
            for n in frontier:
                for m in adj.get(n, []):
                    if m not in seen:
                        seen.add(m)
                        order.append(m)
                        nxt.append(m)
            frontier = nxt
        keep = order
    else:
        start = None
        keep = [t for t, n in nodes.items() if n["kind"] != "scenario"]
    truncated = len(keep) > limit
    keep = keep[:limit]
    ks = set(keep)
    return {
        "focus": start,
        "depth": depth if focus else None,
        "nodes": [nodes[t] for t in keep],
        "edges": [e for e in edges if e["source"] in ks and e["target"] in ks],
        "totals": total,
        "truncated": truncated,
        "note": "derived from the trace's links and the routes scenarios call; scenario "
        "nodes appear only around a focus",
    }


# ---- the tool set -----------------------------------------------------------------------


def _str(args: Mapping[str, Any], key: str, *, required: bool = True) -> str:
    v = args.get(key)
    if v is None or v == "":
        if required:
            raise ToolError(f"missing required argument {key!r}")
        return ""
    if not isinstance(v, str):
        raise ToolError(f"argument {key!r} must be a string")
    return v.strip()


def _targets(args: Mapping[str, Any], key: str = "selected") -> list[str]:
    v = args.get(key)
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ToolError(f"argument {key!r} must be an array of strings")
    return [canon_target(x) for x in v]


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + "..."


IMPLEMENTATION_SECTIONS = ("summary", "rules", "routes", "flows", "machines", "orphans", "excluded")


def _has_implementation_table(root: Path) -> bool:
    """Whether ``root/studio.toml`` has an ``[implementation]`` table (the three Spike C tools
    are registered only then)."""
    try:
        cfg = tomllib.loads((root / "studio.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return isinstance(cfg.get("implementation"), dict)


IMPACT_SECTIONS = ("summary", "tier1", "tier2", "tier3", "flows", "unread", "decidability")
IMPACT_OBSERVE_TIMEOUT = 900.0


def _has_impact_table(root: Path) -> bool:
    """Whether ``root/studio.toml`` has an ``[impact]`` table (the ``change_impact`` tool is
    registered only then)."""
    try:
        cfg = tomllib.loads((root / "studio.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return isinstance(cfg.get("impact"), dict)


class Toolbox:
    """The tools over one backend. ``readonly`` hides change_propose and check_run."""

    def __init__(self, backend: Any, *, readonly: bool = False) -> None:
        self.backend = backend
        self.readonly = readonly
        self._tools: dict[str, dict[str, Any]] = {}
        self._build()

    @property
    def root(self) -> Path:
        return self.backend.root

    def _reg(
        self,
        name: str,
        description: str,
        props: dict[str, Any],
        required: Sequence[str],
        fn: Callable[[Mapping[str, Any]], dict[str, Any]],
        *,
        writes: bool = False,
        read_only_hint: bool = True,
    ) -> None:
        self._tools[name] = {
            "def": {
                "name": name,
                "description": description,
                "inputSchema": {
                    "type": "object",
                    "properties": props,
                    "required": list(required),
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": read_only_hint, "openWorldHint": False},
            },
            "fn": fn,
            "writes": writes,
        }

    def list_tools(self) -> list[dict[str, Any]]:
        return [t["def"] for t in self._tools.values() if not (self.readonly and t["writes"])]

    def names(self) -> list[str]:
        return [d["name"] for d in self.list_tools()]

    def call(self, name: str, args: Mapping[str, Any] | None) -> dict[str, Any]:
        t = self._tools.get(name)
        if t is None or (self.readonly and t["writes"]):
            raise KeyError(name)
        if args is not None and not isinstance(args, Mapping):
            raise ToolError("arguments must be an object")
        args = dict(args or {})
        props = t["def"]["inputSchema"]["properties"]
        unknown = sorted(set(args) - set(props))
        if unknown:
            raise ToolError(f"unknown argument(s): {', '.join(unknown)}")
        return t["fn"](args)

    # -- helpers
    def _view(self) -> ModelView:
        return ModelView(self.backend.model())

    def _build(self) -> None:
        b = self.backend
        S = {"type": "string"}

        def project_status(a: Mapping[str, Any]) -> dict[str, Any]:
            root = self.root
            missing = [n for n in SPEC_FILES if not (root / n).is_file()]
            out: dict[str, Any] = {
                "project": str(root),
                "initialised": not missing,
                "missing": missing,
                "backend": getattr(b, "kind", "?"),
                "has_studio_toml": (root / "studio.toml").is_file(),
            }
            if missing:
                out["next_steps"] = [
                    "Write REQUIREMENTS.md: the rules of the app in plain language.",
                    "Run `lattice spec draft` here to draft spec.py and spec.trace.json.",
                    "Run `lattice studio .` to review the spec and its questions.",
                ]
            else:
                out["next_steps"] = [
                    "Read the spec with model_get, rule_get and spec_ask; run check_run.",
                ]
            return out

        def model_get(a: Mapping[str, Any]) -> dict[str, Any]:
            section = _str(a, "section", required=False)
            m = b.model()
            if section:
                if section not in (*SECTIONS, "markdown", "project"):
                    raise ToolError(f"section must be one of {', '.join(SECTIONS)}")
                return {"section": section, "data": m.get(section)}
            rules = m.get("rules", [])
            counts: dict[str, int] = {}
            for r in rules:
                counts[r["status"]] = counts.get(r["status"], 0) + 1
            return {
                "project": m.get("project"),
                "rule_count": len(rules),
                "rule_status_counts": counts,
                "rules": [
                    {"id": r["id"], "status": r["status"], "text": _clip(r["text"], 160)}
                    for r in rules
                ],
                "app": m.get("app"),
                "changes": m.get("changes"),
                "tables": [t["name"] for t in (m.get("data_model") or {}).get("tables", [])],
                "sections": list(SECTIONS),
                "hint": "pass section=rules|views|data_model|app|changes for the full part",
            }

        self._reg(
            "project_status",
            "Is this directory a Lattice project (spec files present)? What is missing and the "
            "next steps. Always available, even before a spec exists.",
            {},
            [],
            project_status,
        )
        self._reg(
            "model_get",
            "The Studio model of the project (no LLM): without `section`, an overview "
            "(rule counts, rule texts clipped, app state, changes); with `section`, that part "
            "in full: rules, views (the review views: access matrix, scenarios, invariants, "
            "machines, questions), data_model, app (last check), changes.",
            {"section": {"type": "string", "enum": [*SECTIONS, "markdown"]}},
            [],
            model_get,
        )

        def rule_get(a: Mapping[str, Any]) -> dict[str, Any]:
            rid = canon_target(_str(a, "id")).removeprefix("rule:")
            v = self._view()
            r = v.rules.get(rid)
            if r is None:
                raise ToolError(f"no rule {rid!r}; ids look like R12")
            cl = code_links(self.root, v, f"rule:{rid}")
            return {
                **{k: x for k, x in r.items() if k != "rationale_status"},  # rationale has it
                "question_details": [v.questions[q] for q in r["questions"] if q in v.questions],
                "code_links": cl["links"][:10],
                "code_links_source": cl["source"],
                **self._rule_notes(rid),
            }

        self._reg(
            "rule_get",
            "One rule: text, section, status, trace links, open questions (resolved) and "
            "code locations (grep-derived).",
            {"id": {**S, "description": "rule id, e.g. R44"}},
            ["id"],
            rule_get,
        )

        def spec_ask(a: Mapping[str, Any]) -> dict[str, Any]:
            q = _str(a, "question")
            verify = a.get("verify", False)
            if not isinstance(verify, bool):
                raise ToolError("argument 'verify' must be a boolean")
            out = b.ask({"question": q, "selected": _targets(a), "verify": verify})
            if verify and not b.configured:
                out = {**out, "verify_note": "no app is configured, so nothing was verified"}
            return out

        self._reg(
            "spec_ask",
            "Ask what the spec says (the answer card of POST /api/ask: kind yes|no|value|"
            "not_specified, short answer, explanation, checked citations). Uses the LLM "
            "(can take a minute). `verify` runs a generated scenario against the app and "
            "only works when studio.toml configures one.",
            {
                "question": S,
                "selected": {
                    "type": "array",
                    "items": S,
                    "description": "targets in focus: rule:R44, scenario:x, cell:row|GET /p",
                },
                "verify": {"type": "boolean"},
            },
            ["question"],
            spec_ask,
        )

        def spec_lint(_a: Mapping[str, Any]) -> dict[str, Any]:
            items = b.lint()
            return {"count": len(items), "findings": items}

        self._reg(
            "spec_lint",
            "The spec's lint findings (code, element, message, fix).",
            {},
            [],
            spec_lint,
        )

        def check_run(a: Mapping[str, Any]) -> dict[str, Any]:
            layers = _str(a, "layers", required=False) or None
            if layers and not re.fullmatch(r"[ABCDX]+", layers):
                raise ToolError("layers must be letters from ABCDX, e.g. ACDX")
            t = a.get("timeout", DEFAULT_CHECK_TIMEOUT)
            if isinstance(t, bool) or not isinstance(t, int | float) or t <= 0:
                raise ToolError("argument 'timeout' must be a positive number of seconds")
            r = b.check(layers, float(t))
            findings = r.get("findings") or []
            return {
                "verdict": r.get("verdict"),
                "counts": r.get("counts"),
                "at": r.get("at"),
                "seconds": r.get("seconds"),
                "error": r.get("error"),
                "already_running": r.get("already_running", False),
                "failing_targets": sorted(
                    k for k, v in (r.get("targets") or {}).items() if v == "fail"
                ),
                "finding_count": len(findings),
                "findings": findings,
            }

        self._reg(
            "check_run",
            "Run the project's full check of the spec against its app (Studio's /api/check) "
            "and wait for it: verdict, counts per layer, findings. Long-running; `timeout` "
            "in seconds (default 600). Needs an app in studio.toml.",
            {
                "layers": {**S, "description": "layers to run, from ABCDX (default: studio.toml)"},
                "timeout": {"type": "number", "description": "seconds to wait (default 600)"},
            },
            [],
            check_run,
            writes=True,
            read_only_hint=False,
        )

        def code_links_tool(a: Mapping[str, Any]) -> dict[str, Any]:
            return code_links(self.root, self._view(), _str(a, "target"))

        self._reg(
            "code_links",
            "Code locations serving a target (rule:Rn or Rn, scenario:, cell:row|METHOD /p, "
            "endpoint, profile:Table): {path,line_from,line_to,symbol,why}. `source` says "
            "how they were derived (the real Studio has no code index: trace + grep).",
            {"target": S},
            ["target"],
            code_links_tool,
        )

        def graph_tool(a: Mapping[str, Any]) -> dict[str, Any]:
            focus = _str(a, "focus", required=False) or None
            depth = a.get("depth", 1)
            if isinstance(depth, bool) or not isinstance(depth, int) or not 0 <= depth <= 6:
                raise ToolError("argument 'depth' must be an integer 0..6")
            return graph_get(self._view(), focus, depth)

        self._reg(
            "graph_get",
            "The neighbourhood of a rule, endpoint or table in the spec's graph (rules, "
            "endpoints, tables, machines, services, invariants, scenarios; edges traces, "
            "transitions, applies_to, enforces). `focus` accepts rule:R44, R44, "
            "cell:row|METHOD /p, 'POST /runs/{}/cancel', profile:Table.",
            {"focus": S, "depth": {"type": "integer", "minimum": 0, "maximum": 6}},
            [],
            graph_tool,
        )

        def change_propose(a: Mapping[str, Any]) -> dict[str, Any]:
            from lattice.studio.mcp_intake import SourceError, parse_source

            req = _str(a, "request")
            body: dict[str, Any] = {"request": req, "selected": _targets(a)}
            try:
                source = parse_source(a.get("source"))
            except SourceError as e:
                raise ToolError(str(e)) from None
            if source:
                body["source"] = source  # API v1.13: where the request came from
            out = b.new_change(body)
            cid = out["id"]
            c = b.get_change(cid)
            return {
                "id": cid,
                "status": c.get("status"),
                "note": "a pending change set was started; it is never applied from here. "
                "Poll change_get; the owner reviews and accepts it in Lattice Studio.",
            }

        self._reg(
            "change_propose",
            "Propose a change to the requirements, the way Studio's 'ask for a change' does: "
            "drafts the new markdown/spec/trace in the background and checks it. Returns "
            "{id, status}; poll change_get. Never accepts: a human accepts in Studio. Use "
            "this instead of editing REQUIREMENTS.md, spec.py or spec.trace.json. When the "
            "request comes from an external item (an issue, a document, a message read through "
            "another MCP server), pass `source` so the change set records where it came from.",
            {
                "request": {**S, "description": "the change in plain words"},
                "selected": {"type": "array", "items": S},
                "source": {
                    "type": "object",
                    "description": "the external item the request came from",
                    "properties": {
                        "server": {**S, "description": "the MCP server, e.g. claude.ai Linear"},
                        "item": {**S, "description": "the item's id, e.g. PROJ-123"},
                        "url": {**S, "description": "http(s) link to the item (optional)"},
                        "title": {**S, "description": "the item's title (optional)"},
                    },
                    "required": ["server", "item"],
                    "additionalProperties": False,
                },
            },
            ["request"],
            change_propose,
            writes=True,
            read_only_hint=False,
        )

        def change_get(a: Mapping[str, Any]) -> dict[str, Any]:
            c = b.get_change(_str(a, "id"))
            c = dict(c)
            if isinstance(c.get("markdown_diff"), str):
                c["markdown_diff"] = _clip(c["markdown_diff"], 6000)
            return c

        self._reg(
            "change_get",
            "A change set's state: status (drafting|checking|ready|failed|accepted|rejected|"
            "cancelled), markdown diff, rule diff, view diff, checks (validity, lint, "
            "conflicts, impact), questions.",
            {"id": S},
            ["id"],
            change_get,
        )

        def change_list(_a: Mapping[str, Any]) -> dict[str, Any]:
            ch = b.model().get("changes") or []
            return {"count": len(ch), "changes": ch}

        self._reg("change_list", "The change sets: id, request, status.", {}, [], change_list)
        if _has_implementation_table(self.root):
            self._build_implementation()
        self._build_notes()
        if _has_impact_table(self.root):
            self._build_impact()

    # -- the owner's rationale and glossary (Spike G): read-only, only when a file exists
    def _notes_paths(self) -> Any:
        """The four rationale / glossary files beside the project's trace (None: no trace)."""
        from lattice.spec.rationale import notes_paths

        return notes_paths(self.root / "spec.trace.json")

    def _notes(self, *, rationale: bool = True, glossary: bool = True) -> tuple[Any, Any]:
        """(Trace, Notes or None) of the project, loading only the requested files;
        ``bad_rationale`` / ``bad_glossary`` / ``no_spec`` become tool errors (D14)."""
        from lattice.spec.glossary import Notes, load_glossary
        from lattice.spec.rationale import NotesError, load_set
        from lattice.spec.rules import Trace

        try:
            trace = Trace.from_json((self.root / "spec.trace.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise ToolError(f"no_spec: cannot read the project's trace ({e})") from None
        paths = self._notes_paths()
        if paths is None or not paths.any:
            return trace, None
        try:
            return trace, Notes(
                paths,
                load_set(trace, paths) if rationale else None,
                load_glossary(paths) if glossary else None,
            )
        except NotesError as e:
            raise ToolError(str(e)) from None

    def _rule_notes(self, rid: str) -> dict[str, Any]:
        """rule_get's extra keys: ``rationale`` (state; why / tradeoff / owner when approved) and
        ``terms`` (the approved terms the rule uses). Empty without any rationale / glossary
        file."""
        paths = self._notes_paths()
        if paths is None or not paths.any:
            return {}
        trace, notes = self._notes(rationale=paths.has_rationale, glossary=paths.has_glossary)
        out: dict[str, Any] = {}
        if notes is not None and notes.rationale is not None:
            rs = notes.rationale
            ap = rs.approved_for(rid)
            out["rationale"] = {"state": rs.status(rid)}
            if ap is not None:
                n = ap.note
                out["rationale"].update(
                    why=n.why, tradeoff=n.tradeoff, owner=n.owner, approved_by=n.approved_by
                )
        if notes is not None and notes.glossary is not None:
            from lattice.spec.glossary import analyse, term_view, terms_by_rule

            spec, _trace = self._spec_trace()
            used = terms_by_rule(analyse(spec, trace), notes.glossary)
            out["terms"] = [
                {k: term_view(t)[k] for k in ("term", "slug", "definition")}
                for t in used.get(rid, [])
            ]
        return out

    def _build_notes(self) -> None:
        paths = self._notes_paths()
        if paths is None:
            return
        S = {"type": "string"}
        RO = "Read-only: nothing is written, approved or rejected from here."

        def limit_of(a: Mapping[str, Any]) -> int:
            limit = a.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
                raise ToolError("bad_limit: limit must be an integer 1..200")
            return limit

        if paths.has_rationale:

            def rationale_get(a: Mapping[str, Any]) -> dict[str, Any]:
                from lattice.spec.rationale import STATES, empty_set, note_view

                limit = limit_of(a)
                rule = canon_target(_str(a, "rule", required=False)).removeprefix("rule:")
                state = _str(a, "state", required=False)
                if state and state not in STATES:
                    raise ToolError(f"bad_state: state must be one of {', '.join(STATES)}")
                trace, notes = self._notes(glossary=False)
                rs = notes.rationale if notes is not None and notes.rationale else None
                rs = rs or empty_set(trace)
                if rule and rule not in {r.id for r in trace.rules}:
                    raise ToolError(f"unknown_rule: no rule {rule!r}; ids look like R12")
                rows = [
                    note_view(r)
                    for r in rs.resolved
                    if (not rule or rule in (r.note.rule, r.rule))
                    and (not state or r.state == state)
                ]
                m = rs.metrics()
                out: dict[str, Any] = {
                    "file": m["file"],
                    "metrics": m,
                    "total": len(rows),
                    "returned": min(len(rows), limit),
                    "notes": rows[:limit],
                    "proposals": {
                        "proposed": sum(1 for p in rs.proposals if p.status == "proposed"),
                        "rejected": sum(1 for p in rs.proposals if p.status == "rejected"),
                    },
                    "note": "only current or moved notes carry the owner's reason; proposals are "
                    "counted, never shown. " + RO,
                }
                if rule:
                    out["rule"] = rule
                    out["rule_state"] = rs.status(rule)
                return out

            self._reg(
                "rationale_get",
                "The owner's approved rationale (why a rule exists, its trade-off, who decided): "
                "notes with their state (current|moved|edited|ambiguous|stale; only current or "
                "moved carry text), coverage metrics (RC, RN, RP) and the proposals as counts, "
                "never as text. `rule` shows one rule's state (approved|proposed_only|stale|"
                "ambiguous|edited|none). If a rule has none, say no rationale is recorded: never "
                "offer a reason as the owner's. " + RO,
                {
                    "rule": {**S, "description": "a rule id, e.g. R63"},
                    "state": {"type": "string", "enum": ["current", "moved", "edited",
                                                          "ambiguous", "stale"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                [],
                rationale_get,
            )  # fmt: skip

        if paths.has_glossary:

            def glossary_get(a: Mapping[str, Any]) -> dict[str, Any]:
                from lattice.spec.glossary import run_lint, term_view

                limit = limit_of(a)
                lint = a.get("lint", False)
                if not isinstance(lint, bool):
                    raise ToolError("argument 'lint' must be a boolean")
                name = _str(a, "term", required=False)
                spec, _t = self._spec_trace()
                trace, notes = self._notes()
                gset = notes.glossary if notes is not None else None
                if name:
                    t = gset.term_by(name) if gset is not None else None
                    if t is None:
                        raise ToolError(f"unknown_term: no approved term {name!r}")
                    return {"term": term_view(t), "note": RO}
                res = run_lint(spec, trace, notes, self._notes_paths())
                if lint:
                    return {
                        "file": res.metrics["file"],
                        "metrics": res.metrics,
                        "total": len(res.findings),
                        "returned": min(len(res.findings), limit),
                        "findings": res.findings[:limit],
                        "note": "G001 undefined term, G002 unused approved term, G003 a note or "
                        "term that is not current, G004 a proposal pending. " + RO,
                    }
                terms = [term_view(t) for t in (gset.defining if gset is not None else [])]
                return {
                    "file": res.metrics["file"],
                    "metrics": res.metrics,
                    "total": len(terms),
                    "returned": min(len(terms), limit),
                    "terms": terms[:limit],
                    "ignore": list(gset.ignore) if gset is not None else [],
                    "proposals": {"proposed": res.metrics["GP"]},
                    "note": "only owner-approved terms define a word; proposals are counted, "
                    "never shown. " + RO,
                }

            self._reg(
                "glossary_get",
                "The owner's approved business glossary: the terms with their definitions and "
                "coverage metrics (GU, GT, GR, GC, GA, GP, GX); with `lint` the findings (G001 "
                "undefined term, G002 unused term, G003 not current, G004 proposal pending); with "
                "`term` one approved term. Proposals are counted, never shown. " + RO,
                {
                    "term": {**S, "description": "a term, alias or slug, e.g. active-window"},
                    "lint": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                [],
                glossary_get,
            )

    # -- the implementation diff (Spike C): three read-only tools, only with [implementation]
    def _spec_trace(self) -> tuple[Any, Any]:
        """The project's Spec and Trace, read from its files (no write anywhere); kept in memory
        while the files do not change."""
        import hashlib

        from lattice.spec.draft import load_spec
        from lattice.spec.rules import Trace

        files = [self.root / "spec.py", self.root / "spec.trace.json"]
        try:
            module, trace_text = (f.read_text(encoding="utf-8") for f in files)
        except OSError as e:
            raise ToolError(
                f"no_spec: cannot read the project's spec files ({e.strerror or e})"
            ) from None
        key = hashlib.sha256((module + "\0" + trace_text).encode()).hexdigest()
        hit = getattr(self, "_spec_cache", None)
        if hit is not None and hit[0] == key:
            return hit[1], hit[2]
        spec, errors = load_spec(module)
        if spec is None:
            raise ToolError("no_spec: spec.py does not load: " + "; ".join(errors))
        trace = Trace.from_json(trace_text)
        self._spec_cache = (key, spec, trace)
        return spec, trace

    def _impl_ctx(self) -> Any:
        from lattice.spec import implementation as impl

        cfg = impl.implementation_config(self.root)
        if cfg is None or cfg["inventory"] is None:
            raise ToolError("inventory_missing: studio.toml [implementation] names no inventory")
        try:
            inv = impl.load_inventory(cfg["inventory"], cfg["app_root"], cfg["package"])
        except impl.ImplementationError as e:
            raise ToolError(str(e)) from None
        spec, trace = self._spec_trace()
        return impl.ImplContext.of(spec, trace, inv)

    def _build_implementation(self) -> None:
        from lattice.spec import implementation as impl

        S = {"type": "string"}
        RO = "Read-only: nothing is written, the extractor, go and the app are not run."

        def implementation_get(a: Mapping[str, Any]) -> dict[str, Any]:
            section = _str(a, "section", required=False) or "summary"
            if section not in IMPLEMENTATION_SECTIONS:
                raise ToolError(
                    f"bad_section: section must be one of {', '.join(IMPLEMENTATION_SECTIONS)}"
                )
            limit = a.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
                raise ToolError("bad_limit: limit must be an integer 1..200")
            rule, element, status = (
                _str(a, k, required=False) for k in ("rule", "element", "status")
            )
            ctx = self._impl_ctx()
            rep = ctx.report
            fs = impl.findings(ctx)
            inv = rep["inventory"]
            if section == "summary":
                out: dict[str, Any] = {
                    "section": "summary",
                    "requirement_basis": rep["requirement_basis"],
                    "inventory": {
                        "extract": inv["extract_sha256"][:12],
                        "workflows": (inv["workflows_sha256"] or "none")[:12],
                        "run_mode": inv["run_mode"],
                        "stale": bool(inv["stale"]),
                        "workflows_mismatch": inv["workflows_mismatch"],
                    },
                    "summary": rep["summary"],
                    "findings_total": len(fs),
                    "findings": fs[:3],
                    "sections": list(IMPLEMENTATION_SECTIONS),
                    "note": "static facts from the code unless marked observed; absence is "
                    "inferred. "
                    "Ask for section=rules|orphans|... to see more. " + RO,
                }
                while len(json.dumps(out)) > 3800 and out["findings"]:
                    out["findings"] = out["findings"][:-1]
                return out
            by_id = {f["target"]: f for f in fs}
            sect = "rules" if "rules" in rep else "requirements"
            if section == "rules":
                items = [
                    e
                    for e in rep[sect]
                    if (not rule or e["id"] == rule)
                    and (not status or e["status"] == status)
                    and (not element or e["id"] in rep["by_element"].get(element, []))
                ]
                rows = []
                for e in items[:limit]:
                    row = {k: e[k] for k in ("id", "spec_status", "status") if k in e}
                    for k in ("missing", "partial", "reasons", "impact_label", "last_check"):
                        if k in e:
                            row[k] = e[k]
                    if rule:
                        row["elements"] = e["elements"]
                    if e["id"] in by_id and e["status"] == "required_not_implemented":
                        row["finding"] = by_id[e["id"]]
                    rows.append(row)
            elif section == "routes":
                items = [
                    r
                    for r in rep["routes"]
                    if (not status or r["status"] == status)
                    and (not element or element.removeprefix("impl:route:") == r["key"])
                    and (not rule or rule in r["rules"])
                ]
                rows = items[:limit]
            elif section == "flows":
                items = [
                    f
                    for f in rep["flows"]
                    if (not status or f["effective"] == status)
                    and (not element or element.removeprefix("impl:route:") in f["routes"])
                    and (not rule or rule in f["rules"])
                ]
                rows = [{k: v for k, v in f.items() if k != "evidence"} for f in items[:limit]]
            elif section == "machines":
                items = [m for m in rep["machines"] if not rule or rule in m["rules"]]
                rows = items[:limit]
            elif section == "orphans":
                items = [f for f in fs if f["kind"] == "implemented_not_required"]
                rows = items[:limit]
            else:
                items = rep["excluded"]
                rows = items[:limit]
            return {
                "section": section,
                "inventory": {"stale": bool(inv["stale"]), "run_mode": inv["run_mode"]},
                "total": len(items),
                "returned": len(rows),
                "items": rows,
                "note": RO,
            }

        self._reg(
            "implementation_get",
            "What the app's code implements against what the spec requires (the implementation "
            "diff). `section`: summary (default, counts and the first findings), rules (status per "
            "rule: implemented_verified|implemented_unverified|required_not_implemented|"
            "no_element), routes, flows, machines, orphans (implemented but required by no rule), "
            "excluded. Filters: rule, element (impl:route:METHOD /path), status, limit (<= 200). "
            "Each finding carries a `proposal` for change_propose: text only, never sent from "
            "here. "
            "Facts are static (from the code) unless basis is observed (a replayed flow); an "
            "absence is inferred.",
            {
                "section": {"type": "string", "enum": list(IMPLEMENTATION_SECTIONS)},
                "rule": {**S, "description": "a rule id, e.g. R44"},
                "element": {**S, "description": "an element key, e.g. impl:route:GET /v1/me"},
                "status": S,
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            [],
            implementation_get,
        )

        def workflow_get(a: Mapping[str, Any]) -> dict[str, Any]:
            ident = _str(a, "id")
            ctx = self._impl_ctx()
            try:
                return impl.get_workflow(ctx, ident)
            except impl.ImplementationError as e:
                raise ToolError(str(e)) from None

        self._reg(
            "workflow_get",
            "One flow (wf-...) or machine (wm-... or table.column) of the implementation: steps, "
            "status, evidence (code:path:line), the rules that need it and the spec skeletons it "
            "matches. A flow name is inferred. " + RO,
            {"id": {**S, "description": "a flow or machine id from implementation_get"}},
            ["id"],
            workflow_get,
        )

        def workflow_find(a: Mapping[str, Any]) -> dict[str, Any]:
            q = _str(a, "question")
            limit = a.get("limit", 5)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
                raise ToolError("bad_limit: limit must be an integer 1..10")
            return impl.find_candidates(self._impl_ctx(), q, limit)

        self._reg(
            "workflow_find",
            "Find the routes, flows, machines and unimplemented rules a question is about "
            "(deterministic, lexical: exact METHOD /path mentions first, then term overlap). "
            "`hint`: exists, spec_only, no_match or several. Lexical match, not understanding. "
            + RO,
            {"question": S, "limit": {"type": "integer", "minimum": 1, "maximum": 10}},
            ["question"],
            workflow_find,
        )

    # -- change impact (Spike D): one read-only tool, only with [impact]
    def _impact_candidate(self, a: Mapping[str, Any]) -> tuple[str | None, Path | None]:
        change, candidate = (_str(a, k, required=False) for k in ("change", "candidate"))
        if bool(change) == bool(candidate):
            raise ToolError("no_candidate: give exactly one of `change` (cN) and `candidate`")
        if not candidate:
            return change, None
        path = (self.root / candidate).resolve()
        if self.root.resolve() not in (path, *path.parents) or not path.is_dir():
            raise ToolError(f"no_candidate: {candidate!r} is not a directory of this project")
        return None, path

    def _impact_observe(self, change: str | None, candidate: Path | None) -> dict[str, Any]:
        """The CLI as a subprocess (tier 1 too): the harness's chdir and sys.path never touch
        this server. A fresh container owner, a timeout, a process-group kill, and the owner's
        containers removed in `finally`."""
        import contextlib
        import signal
        import subprocess
        import sys
        import uuid

        from lattice.studio.state import remove_owned_containers

        token = f"impact-{uuid.uuid4().hex[:12]}"
        argv = [sys.executable, "-m", "lattice.cli", "spec", "impact", "--project", str(self.root)]
        argv += ["--candidate", str(candidate)] if candidate else ["--change", str(change)]
        argv += ["--tiers", "123", "--json"]
        env = dict(os.environ, LATTICE_CONTAINER_OWNER=token)
        proc = subprocess.Popen(  # noqa: S603
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
        try:
            out, err = proc.communicate(timeout=IMPACT_OBSERVE_TIMEOUT)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise ToolError(
                f"timeout: the observed run took more than {int(IMPACT_OBSERVE_TIMEOUT)}s"
            ) from None
        finally:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
            remove_owned_containers(token)
        if proc.returncode not in (0, 3):
            first = (err.strip().splitlines() or ["failed"])[-1]
            code = first.removeprefix("error: ")
            raise ToolError(code if re.match(r"^[a-z_]+:", code) else f"impact_failed: {code}")
        try:
            return dict(json.loads(out))
        except ValueError:
            raise ToolError("impact_failed: the report is not JSON") from None

    def _build_impact(self) -> None:
        from lattice.spec import impact_report as ir

        S = {"type": "string"}

        def change_impact(a: Mapping[str, Any]) -> dict[str, Any]:
            section = _str(a, "section", required=False) or "summary"
            if section not in IMPACT_SECTIONS:
                raise ToolError(f"bad_section: section must be one of {', '.join(IMPACT_SECTIONS)}")
            limit = a.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
                raise ToolError("bad_limit: limit must be an integer 1..200")
            observe = a.get("observe", False)
            if not isinstance(observe, bool):
                raise ToolError("bad_observe: observe must be a boolean")
            change, candidate = self._impact_candidate(a)
            if observe and self.readonly:
                raise ToolError(
                    "readonly: observe=true runs the app and Docker; a --readonly server "
                    "does not start processes or containers"
                )
            if observe:
                if not ir.project_has_app(self.root):
                    raise ToolError("no_app: studio.toml hooks up no app ([app] app and db)")
                report = self._impact_observe(change, candidate)
            else:
                try:
                    inp = ir.build_inputs(
                        project=self.root,
                        candidate=candidate,
                        change=change,
                        state_dir=ir.default_state_dir(self.root) if change else None,
                    )
                    if inp.inventory is None:
                        raise ToolError(inp.inventory_error or "inventory_missing: no inventory")
                    report = ir.build_report(inp, tiers="23", runner=None, baseline="none")
                except ir.ImpactError as e:
                    raise ToolError(str(e)) from None
            return _impact_view(report, section, limit)

        self._reg(
            "change_impact",
            "What a spec change touches: the code locations and the checks and workflows that "
            "fail, in tiers. Give `change` (a Studio change id, cN, ready or accepted) or "
            "`candidate` (a project-relative directory with the candidate's REQUIREMENTS.md, "
            "spec.py and spec.trace.json). Default: tier 2 (determined from the spec change and "
            "the verified flows) and tier 3 (static code locations from the inventory), a pure "
            "read. `observe: true` also runs the candidate on the app (tier 1, observed; starts "
            "Docker; refused by a --readonly server). Which workflows fail is mostly not "
            "decidable before the change exists: tier 3 flows are `may_fail`, not claims. "
            "`section`: summary (default, under 4k characters), tier1, tier2, tier3, flows, "
            "unread, decidability. Read-only: the project, the spec, the inventory and the state "
            "are never written.",
            {
                "change": {**S, "description": "a Studio change id, e.g. c3"},
                "candidate": {**S, "description": "a project-relative directory"},
                "observe": {"type": "boolean"},
                "section": {"type": "string", "enum": list(IMPACT_SECTIONS)},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            [],
            change_impact,
        )


def _impact_view(report: Mapping[str, Any], section: str, limit: int) -> dict[str, Any]:
    """The tool's answer from a full report: the summary (under 4k characters) or one section,
    at most `limit` items."""
    head = {"schema": report["schema"], "tiers": report["tiers"]}
    t1, t2, t3 = report["tier1"], report["tier2"], report["tier3"]
    if section == "summary":
        out: dict[str, Any] = {
            **head,
            "section": "summary",
            "rules": {
                k: len(v) if isinstance(v, list) else v for k, v in report["diff"]["rules"].items()
            },
            "targets": {k: len(v) for k, v in report["diff"]["targets"].items()},
            "summary": report["summary"]["tiers"],
            "workflows": report["summary"]["workflows"],
            "note": report["summary"]["note"],
            "determined": [d["claim"] for d in t2["determined"][:3]],
            "files": t3.get("files", [])[:10],
            "sections": list(IMPACT_SECTIONS),
        }
        while len(json.dumps(out)) > 3800 and (out["files"] or out["determined"]):
            out["files" if out["files"] else "determined"].pop()
        return out
    if section == "tier1":
        items = t1.get("failing", [])
        extra = {"resolved": t1.get("resolved", [])[:limit], "counts": t1.get("counts", {})}
    elif section == "tier2":
        items = t2.get("determined", [])
        extra = {"flows": t2.get("flows", [])[:limit]}
    elif section == "tier3":
        items = t3.get("locations", [])
        extra = {"counts": t3.get("counts", {}), "external": t3.get("external")}
    elif section == "flows":
        items = [
            *t1.get("flows", []),
            *t2.get("flows", []),
            *t3.get("flows", []),
        ]
        extra = {}
    elif section == "unread":
        items = t3.get("unread", [])
        extra = {}
    else:
        items = t2.get("decidability", [])
        extra = {}
    return {
        **head,
        "section": section,
        "total": len(items),
        "returned": min(len(items), limit),
        "items": items[:limit],
        **extra,
    }


def cap_result(data: Any, cap: int = OUTPUT_CAP) -> tuple[str, bool]:
    """Compact JSON of `data`; over `cap` characters a JSON envelope with a preview."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) <= cap:
        return text, False
    env: dict[str, Any] = {
        "truncated": True,
        "original_chars": len(text),
        "preview": "",
        "hint": "narrow the call (a section, a focus, a smaller depth) to see the rest",
    }
    room = max(cap - 300, 0)
    while True:
        env["preview"] = text[:room]
        out = json.dumps(env, ensure_ascii=False, separators=(",", ":"))
        if len(out) <= cap or room == 0:
            return out, True
        room = max(room - max(len(out) - cap, 50), 0)
