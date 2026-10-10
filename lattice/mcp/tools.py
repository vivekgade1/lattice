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
                **r,
                "question_details": [v.questions[q] for q in r["questions"] if q in v.questions],
                "code_links": cl["links"][:10],
                "code_links_source": cl["source"],
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
            req = _str(a, "request")
            out = b.new_change({"request": req, "selected": _targets(a)})
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
            "this instead of editing REQUIREMENTS.md, spec.py or spec.trace.json.",
            {
                "request": {**S, "description": "the change in plain words"},
                "selected": {"type": "array", "items": S},
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
