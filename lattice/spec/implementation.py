"""`lattice.spec.implementation`: the implementation diff -- what the code implements against
what the spec requires (docs/results/2026-10-15-implementation-diff-prereg.md, D1-D8).

The inventory (``lattice import inventory``) holds Spike A's extract of a Go + Gin app, an
optional Spike B ``workflows.json`` and a hash of the source it was taken from. This module
only *reads* it -- it writes nothing, runs no extractor, no ``go``, no git, no Docker and no
model -- and combines it with a spec (and its trace):

- ``load_inventory``: the inventory directory, re-hashing the source set (staleness);
- ``build_report``: per rule (or, without a trace, per cell and scenario) one of
  ``implemented_verified``, ``implemented_unverified``, ``required_not_implemented`` or
  ``no_element``; the routes, flows and machines the code has that no rule requires
  (``implemented_not_required``); the exclusions, listed with their reason;
- ``check_code``: the deterministic checker of a ``code:<path>:<line>`` citation;
- the CLI ``python -m lattice.spec implementation --spec MOD:SPEC [--trace T] --inventory DIR``.

A status is only as strong as its basis: ``static`` (read from the code), ``observed`` (a
Spike B replay of those exact requests passed on a fresh database) or ``inferred`` (an
absence in a static extract). Linking is the spec's own trace and text, never a guess from
names (D4, D12).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.spec.model import Await, Spec, Step, tables_of
from lattice.spec.review import _build, _normalize_path
from lattice.spec.rules import Trace

SCHEMA_REPORT = "lattice.implementation-report/1"
SCHEMA_INVENTORY = "lattice.implementation-inventory/1"
WORKFLOWS_SCHEMA = "lattice.workflows/1"
INVENTORY_FILES = ("extract.json", "workflows.json", "inventory.json")

STATUSES = (
    "implemented_verified",
    "implemented_unverified",
    "required_not_implemented",
    "no_element",
)
# D6: unlinked routes with exactly these paths (any method), or under the pprof prefix, are
# plumbing -- an explicit, displayed list, never inferred from names
PLUMBING_PATHS = (
    "/health",
    "/healthz",
    "/livez",
    "/readyz",
    "/ready",
    "/ping",
    "/metrics",
    "/version",
)
PLUMBING_PREFIX = "/debug/pprof/"
IGNORED_METHODS = ("HEAD", "OPTIONS")
ANY_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
MAX_CODE_FILE = 1_000_000
MIN_QUOTE = 8
STALE_LIST = 20

# the same pattern as lattice.mcp.tools.routes_in (D4 c)
_ROUTE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s`'\"),;]*)")
_CODE_TARGET = re.compile(r"^code:(.+):(\d+)$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_STATUS_WORD = re.compile(r"\b(verified|unverified|failed)\b")


class ImplementationError(Exception):
    """A read that cannot be answered. `code` is one of ``inventory_missing``,
    ``bad_inventory``, ``bad_workflows``, ``bad_report``; the message starts with it."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.detail = message


# ---- keys ---------------------------------------------------------------------------------


def template_path(path: str) -> str:
    """A route path as a template (D3): ``:x``, ``*x`` and ``{x}`` segments, integers and UUIDs
    become ``{}``; the query string and a trailing ``/`` are dropped."""
    path = path.split("?", 1)[0]
    segs = [
        "{}" if s[:1] in (":", "*") or (s.startswith("{") and s.endswith("}")) else s
        for s in path.split("/")
    ]
    out = _normalize_path("/".join(segs))
    if len(out) > 1:
        out = out.rstrip("/")
    return out or "/"


def route_key(method: str, path: str) -> str:
    """``METHOD /template`` of a route or request path."""
    return f"{method.upper()} {template_path(path)}"


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _tools_consts() -> tuple[frozenset[str], frozenset[str]]:
    from lattice.mcp.tools import _CODE_EXT, _SKIP_DIRS

    return frozenset(_SKIP_DIRS), frozenset(_CODE_EXT)


# ---- the source set (D1) ------------------------------------------------------------------


def source_files(app_root: Path) -> list[str]:
    """The source set under `app_root` (D1): every ``*.go``, ``go.mod`` and ``go.sum``, minus
    ``_SKIP_DIRS``, ``testdata`` and dot-directories; relative ``/`` paths, sorted. Symbolic
    links are not followed and not listed."""
    skip, _ = _tools_consts()
    out: list[str] = []
    root = Path(app_root)
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(
            d
            for d in dns
            if d not in skip
            and d != "testdata"
            and not d.startswith(".")
            and not (Path(dp) / d).is_symlink()
        )
        for fn in fns:
            if not (fn.endswith(".go") or fn in ("go.mod", "go.sum")):
                continue
            p = Path(dp) / fn
            if p.is_symlink() or not p.is_file():
                continue
            out.append(p.relative_to(root).as_posix())
    return sorted(out)


def source_hashes(app_root: Path) -> dict[str, str]:
    """{relative path: sha256} of the source set."""
    root = Path(app_root)
    return {rel: sha256_bytes((root / rel).read_bytes()) for rel in source_files(root)}


def source_record(app_root: Path) -> dict[str, Any]:
    """The inventory's ``source`` entry: ``{sha256, files[{path, sha256}]}``, where the first
    hash covers the sorted ``path sha256`` lines."""
    hashes = source_hashes(app_root)
    lines = "".join(f"{p} {h}\n" for p, h in hashes.items())
    return {
        "sha256": sha256_bytes(lines.encode()),
        "files": [{"path": p, "sha256": h} for p, h in hashes.items()],
    }


def canonical_json(obj: Any) -> str:
    """Sorted keys, one space of indent, a final newline (what the inventory files hold)."""
    return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


# ---- the studio.toml table ----------------------------------------------------------------


def implementation_config(project: Path) -> dict[str, Any] | None:
    """The ``[implementation]`` table of ``project/studio.toml`` resolved (``inventory``,
    ``app_root``, ``package``), or None when there is none (the feature is off)."""
    path = Path(project) / "studio.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    table = data.get("implementation")
    if not isinstance(table, dict):
        return None
    inv = table.get("inventory")
    base = Path(project)

    def resolve(v: Any, default: str | None) -> Path | None:
        if not isinstance(v, str) or not v:
            v = default  # type: ignore[assignment]
        if v is None:
            return None
        p = Path(v).expanduser()
        return p if p.is_absolute() else (base / p)

    return {
        "inventory": resolve(inv, None),
        "app_root": resolve(table.get("app_root"), "."),
        "package": str(table["package"]) if table.get("package") else None,
    }


# ---- the inventory and the workflows adapter (D1, D2) -------------------------------------


@dataclass
class Inventory:
    """A loaded inventory: the extract, the (adapted) workflows and the staleness verdict."""

    directory: Path
    app_root: Path
    package: str
    meta: dict[str, Any]
    extract: dict[str, Any]
    workflows: dict[str, Any] | None
    stale: dict[str, Any] | None
    source_paths: frozenset[str]
    mismatch: str | None = None
    consistency: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def extract_sha256(self) -> str:
        return str(self.meta.get("extract_sha256"))

    @property
    def workflows_sha256(self) -> str | None:
        return self.meta.get("workflows_sha256")

    @property
    def run_mode(self) -> str | None:
        return self.workflows["mode"] if self.workflows else None

    def evidence_path(self, rel: str) -> str:
        """An extract path (relative to the Go module) as a path under the app root."""
        return rel if self.package in (".", "") else f"{self.package.strip('/')}/{rel}"


def _read_json(path: Path, code: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise ImplementationError(code, f"cannot read {path.name}: {err.strerror or err}") from None
    except ValueError as err:
        raise ImplementationError(code, f"{path.name} is not valid JSON ({err})") from None


def _stale_verdict(meta: Mapping[str, Any], app_root: Path) -> dict[str, Any] | None:
    recorded = {f["path"]: f["sha256"] for f in (meta.get("source") or {}).get("files") or []}
    now = source_hashes(app_root)
    changed = sorted(p for p in recorded if p in now and recorded[p] != now[p])
    added = sorted(p for p in now if p not in recorded)
    removed = sorted(p for p in recorded if p not in now)
    if not (changed or added or removed):
        return None
    return {
        "changed": changed[:STALE_LIST],
        "added": added[:STALE_LIST],
        "removed": removed[:STALE_LIST],
        "counts": {"changed": len(changed), "added": len(added), "removed": len(removed)},
    }


def load_inventory(
    directory: Path | str, app_root: Path | str | None = None, package: str | None = None
) -> Inventory:
    """Read an inventory directory (D1). Creates, caches and refreshes nothing; every call
    re-hashes the source set under `app_root` and records what changed. Raises
    :class:`ImplementationError` (``inventory_missing``, ``bad_inventory``)."""
    d = Path(directory)
    if not d.is_dir():
        raise ImplementationError("inventory_missing", f"no inventory directory at {d}")
    if not (d / "inventory.json").is_file():
        raise ImplementationError("inventory_missing", f"{d} has no inventory.json")
    meta = _read_json(d / "inventory.json", "bad_inventory")
    if not isinstance(meta, dict) or meta.get("schema") != SCHEMA_INVENTORY:
        raise ImplementationError(
            "bad_inventory", f"inventory.json schema is not {SCHEMA_INVENTORY!r}"
        )
    ex_path = d / "extract.json"
    if not ex_path.is_file():
        raise ImplementationError("bad_inventory", f"{d} has no extract.json")
    raw_extract = ex_path.read_bytes()
    if sha256_bytes(raw_extract) != meta.get("extract_sha256"):
        raise ImplementationError("bad_inventory", "extract.json does not match its recorded hash")
    extract = _read_json(ex_path, "bad_inventory")
    if not isinstance(extract, dict) or not isinstance(extract.get("routes"), list):
        raise ImplementationError("bad_inventory", "extract.json is not a goextract document")
    root = Path(app_root) if app_root is not None else Path(".")
    if not root.is_dir():
        raise ImplementationError("bad_inventory", f"the app root {root} is not a directory")
    raw_wf: Any = None
    wf_sha = meta.get("workflows_sha256")
    if wf_sha is not None:
        wf_path = d / "workflows.json"
        if not wf_path.is_file():
            raise ImplementationError("bad_inventory", f"{d} has no workflows.json")
        if sha256_bytes(wf_path.read_bytes()) != wf_sha:
            raise ImplementationError(
                "bad_inventory", "workflows.json does not match its recorded hash"
            )
        raw_wf = _read_json(wf_path, "bad_inventory")
    inv = Inventory(
        directory=d,
        app_root=root,
        package=str(package if package is not None else meta.get("package") or "."),
        meta=meta,
        extract=extract,
        workflows=None,
        stale=_stale_verdict(meta, root),
        source_paths=frozenset(f["path"] for f in (meta.get("source") or {}).get("files") or []),
    )
    if raw_wf is not None:
        inv.workflows, inv.mismatch, inv.consistency, inv.notes = read_workflows(raw_wf, inv)
    return inv


def _extract_route_index(extract: Mapping[str, Any]) -> dict[str, set[tuple[str, int]]]:
    """key -> {(file, line)} of the registrations of the extract's routes (ANY expanded)."""
    out: dict[str, set[tuple[str, int]]] = {}
    for r in extract.get("routes") or []:
        method = str(r.get("method") or "").upper()
        if method in IGNORED_METHODS:
            continue
        path = str(r.get("path") or r.get("path_template") or "")
        methods = ANY_METHODS if method == "ANY" else (method,)
        for m in methods:
            out.setdefault(route_key(m, path), set()).add(
                (str(r.get("file")), int(r.get("line") or 0))
            )
    return out


def _parse_at(at: Any) -> tuple[str, int] | None:
    m = re.fullmatch(r"(.+):(\d+)", str(at))
    return (m.group(1), int(m.group(2))) if m else None


def read_workflows(
    raw: Any, inv: Inventory
) -> tuple[dict[str, Any], str | None, str | None, list[str]]:
    """THE adapter (D2): the only reader of the raw ``workflows.json``. Returns
    (normalised workflows, mismatch reason or None, how consistency was decided, notes).

    Normalised: ``{mode, module, flows[{id, name, inferred_name, kind, status, source, reason,
    skeleton, routes, focus_from, machine_refs, steps[{i, kind, method, route, path, caller,
    json, expect_status, role, evidence[], service, expect_db, within_s}]}], machines[{id,
    table, column, model, states, edges[{from, to, source}], status, spec_object,
    evidence[]}]}``. Step routes are keys (``METHOD /a/{}``); any flow status other than
    ``verified`` counts as not verified.
    """
    if not isinstance(raw, dict) or raw.get("schema") != WORKFLOWS_SCHEMA:
        raise ImplementationError(
            "bad_workflows", f"workflows.json schema is not {WORKFLOWS_SCHEMA!r}"
        )
    notes: list[str] = []
    flows: list[dict[str, Any]] = []
    missing_route = 0
    for f in raw.get("flows") or []:
        skeleton = [str(s) for s in f.get("skeleton") or []]
        steps: list[dict[str, Any]] = []
        req_i = 0
        for s in f.get("steps") or []:
            if s.get("kind") == "await":
                steps.append(
                    {
                        "i": s.get("i"),
                        "kind": "await",
                        "service": s.get("service"),
                        "expect_db": s.get("expect_db"),
                        "within_s": s.get("within_s"),
                    }
                )
                continue
            if s.get("route"):
                m, _, p = str(s["route"]).partition(" ")
                key = route_key(m, p)
            elif req_i < len(skeleton):  # fallback: the skeleton's req_i-th entry
                key = skeleton[req_i]
                missing_route += 1
            else:
                key = route_key(str(s.get("method") or ""), str(s.get("path") or ""))
            req_i += 1
            steps.append(
                {
                    "i": s.get("i"),
                    "kind": "request",
                    "method": str(s.get("method") or "").upper(),
                    "route": key,
                    "path": s.get("path"),
                    "caller": s.get("caller"),
                    "json": s.get("json"),
                    "expect_status": s.get("expect_status"),
                    "role": s.get("role"),
                    "evidence": sorted(
                        {str(e.get("at")) for e in s.get("evidence") or [] if e.get("at")}
                    ),
                    "evidence_route": sorted(
                        {
                            str(e.get("at"))
                            for e in s.get("evidence") or []
                            if e.get("what") == "route" and e.get("at")
                        }
                    ),
                }
            )
        flows.append(
            {
                "id": str(f.get("id")),
                "name": str(f.get("name") or ""),
                "inferred_name": True
                if f.get("inferred_name") is None
                else bool(f["inferred_name"]),
                "kind": str(f.get("kind") or ""),
                "status": str(f.get("status") or ""),
                "source": f.get("source"),
                "reason": str(f.get("reason") or ""),
                "skeleton": skeleton,
                "routes": [str(r) for r in f.get("routes") or []],
                "focus_from": int(f.get("focus_from") or 0),
                "machine_refs": [str(m) for m in f.get("machine_refs") or []],
                "steps": steps,
            }
        )
    if missing_route:
        notes.append(f"{missing_route} step routes taken from the skeleton")
    machines: list[dict[str, Any]] = []
    for m in raw.get("machines") or []:
        so = m.get("spec_object")
        machines.append(
            {
                "id": str(m.get("id")),
                "table": str(m.get("table")),
                "column": str(m.get("column")),
                "model": m.get("model"),
                "states": [str(s) for s in m.get("states") or []],
                "edges": [
                    {"from": str(e.get("from")), "to": str(e.get("to")), "source": e.get("source")}
                    for e in m.get("edges") or []
                ],
                "status": str(m.get("status") or ""),
                "spec_object": (
                    {"table": so.get("table"), "column": so.get("column")}
                    if isinstance(so, Mapping)
                    else None
                ),
                "evidence": sorted(
                    {
                        str(ev.get("at"))
                        for e in m.get("edges") or []
                        for ev in e.get("evidence") or []
                        if ev.get("at")
                    }
                ),
            }
        )
    flows.sort(key=lambda f: f["id"])
    machines.sort(key=lambda m: (m["table"], m["column"]))
    run = raw.get("run") or {}
    out = {
        "mode": str(run.get("mode") or "static"),
        "module": (raw.get("app") or {}).get("module"),
        "flows": flows,
        "machines": machines,
    }
    # consistency with the extract (D2): the recorded hash, else the route-set comparison
    app = raw.get("app") or {}
    mismatch: str | None = None
    how: str
    if app.get("extract_sha256") == inv.extract_sha256:
        how = "sha256"
    else:
        how = "route-set"
        notes.append("app.extract_sha256 hashes the extractor's own output; compared the route set")
        idx = _extract_route_index(inv.extract)
        if out["module"] != inv.extract.get("module"):
            mismatch = (
                f"module {out['module']!r} is not the extract's {inv.extract.get('module')!r}"
            )
        else:
            for f in flows:
                for s in f["steps"]:
                    if s["kind"] != "request":
                        continue
                    regs = idx.get(s["route"])
                    if regs is None:
                        mismatch = (
                            f"flow {f['id']} step {s['i']} route {s['route']} is not in the extract"
                        )
                        break
                    for at in s["evidence_route"]:
                        loc = _parse_at(at)
                        if loc is not None and loc not in regs:
                            mismatch = (
                                f"flow {f['id']} step {s['i']} registers {s['route']} at {at}, "
                                "the extract at another line"
                            )
                            break
                    if mismatch:
                        break
                if mismatch:
                    break
    return out, mismatch, how, notes


# ---- the code side: elements of the extract (D3) ------------------------------------------


@dataclass
class CodeView:
    """The inventory seen as elements: routes (by key), tables, the machines Spike B found and
    the (table, column) pairs the extract sees written."""

    inv: Inventory
    routes: list[dict[str, Any]]
    by_key: dict[str, list[dict[str, Any]]]
    conditional: frozenset[str]
    tables: dict[str, dict[str, Any]]
    machines: list[dict[str, Any]]
    writes: frozenset[tuple[str, str]]

    def loc(self, file: Any, line: Any) -> str:
        return f"code:{self.inv.evidence_path(str(file))}:{int(line or 0)}"

    def machine_for(self, spec_table: str, sql_table: str, column: str) -> dict[str, Any] | None:
        """The workflow machine of a spec machine (D4): `spec_object` equal to (T, c), else
        the machine on (SQL name of T, c)."""
        for m in self.machines:
            so = m["spec_object"]
            if so and so.get("table") == spec_table and so.get("column") == column:
                return m
        for m in self.machines:
            if m["table"] == sql_table and m["column"] == column:
                return m
        return None


def code_view(inv: Inventory) -> CodeView:
    ex = inv.extract
    routes: list[dict[str, Any]] = []
    by_key: dict[str, list[dict[str, Any]]] = {}
    conditional: set[str] = set()
    for r in ex.get("routes") or []:
        method = str(r.get("method") or "").upper()
        if method in IGNORED_METHODS:
            continue
        path = str(r.get("path") or r.get("path_template") or "")
        tpl = template_path(path)
        via_any = method == "ANY"
        refusals = sorted(
            {
                (int(x["status"]), str(x.get("code") or x.get("detail") or ""))
                for x in [*(r.get("raises") or []), *(r.get("guards") or [])]
                if isinstance(x.get("status"), int) and x["status"] >= 400
            }
        )
        h = r.get("handler") or {}
        entry = {
            "id": f"{method} {tpl}",
            "method": method,
            "path": tpl,
            "gin_path": path,
            "file": r.get("file"),
            "line": r.get("line"),
            "handler": h.get("func"),
            "handler_file": h.get("file"),
            "handler_line": h.get("line"),
            "auth": r.get("auth"),
            "status_codes": sorted(
                int(s) for s in r.get("status_codes") or [] if isinstance(s, int)
            ),
            "refusals": refusals,
            "condition": r.get("condition"),
            "via_any": via_any,
            "tables": sorted(str(t) for t in r.get("tables") or []),
            "keys": [f"{m} {tpl}" for m in (ANY_METHODS if via_any else (method,))],
        }
        routes.append(entry)
        for k in entry["keys"]:
            by_key.setdefault(k, []).append(entry)
            if entry["condition"]:
                conditional.add(k)
    for entry in routes:
        entry["collision"] = any(len(by_key[k]) > 1 for k in entry["keys"]) and not entry["via_any"]
    routes.sort(key=lambda e: (e["path"], e["method"], str(e["file"]), int(e["line"] or 0)))
    tables = {str(t.get("table")): t for t in ex.get("tables") or [] if t.get("table")}
    machines = list(inv.workflows["machines"]) if inv.workflows else []
    writes = frozenset(
        (str(w.get("table")), str(w.get("column"))) for w in ex.get("status_writes") or []
    )
    return CodeView(inv, routes, by_key, frozenset(conditional), tables, machines, writes)


def route_evidence(cv: CodeView, r: Mapping[str, Any]) -> list[str]:
    out = [cv.loc(r["file"], r["line"])]
    if r.get("handler_file"):
        out.append(cv.loc(r["handler_file"], r["handler_line"]))
    return out


# ---- the spec side: endpoints, rule links (D4) --------------------------------------------


@dataclass
class SpecView:
    """What the spec and its trace say, in the report's keys."""

    spec: Spec
    trace: Trace | None
    rule_rows: list[Any]
    cells: dict[str, str]  # cell target -> route key
    scenarios: dict[str, list[str]]  # scenario name -> request step keys, in order
    sql_names: dict[str, str]  # spec table -> SQL name
    tables: frozenset[str]

    @classmethod
    def of(cls, spec: Spec, trace: Trace | None) -> SpecView:
        data = _build(spec, trace or Trace(source="", rules=[]), None, None, "spec")
        cells: dict[str, str] = {}
        for group in data.access_groups:
            for row in group.rows:
                for col in group.columns:
                    if group.cells.get((row, col), "·") == "·":
                        continue
                    m, _, p = col.partition(" ")
                    cells[f"cell:{row}|{col}"] = route_key(m, p)
        scenarios = {
            sc.name: [route_key(s.method, s.path) for s in sc.steps if isinstance(s, Step)]
            for sc in spec.scenarios
        }
        tabs, sql = tables_of(spec.profile)
        return cls(
            spec,
            trace,
            list(data.rule_rows) if trace is not None else [],
            cells,
            scenarios,
            dict(sql),
            frozenset(tabs),
        )

    def sql(self, table: str) -> str:
        return self.sql_names.get(table, table)

    def invariant_tables(self, name: str) -> tuple[list[str], bool]:
        """(tables named in the invariant's expression, whether it parsed)."""
        inv = next((i for i in self.spec.invariants if i.name == name), None)
        if inv is None:
            return [], False
        try:
            tree = ast.parse(inv.expr, mode="eval")
        except SyntaxError:
            return [], False
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in self.tables}
        return sorted(names), True

    def skeletons(self, conditional: Iterable[str]) -> list[list[str]]:
        """The spec's multi-route skeletons: each scenario's request routes, conditional ones
        dropped and consecutive repeats collapsed; two or more routes; distinct."""
        cond = set(conditional)
        seen: dict[tuple[str, ...], None] = {}
        for steps in self.scenarios.values():
            sk: list[str] = []
            for k in steps:
                if k in cond or (sk and sk[-1] == k):
                    continue
                sk.append(k)
            if len(sk) >= 2:
                seen.setdefault(tuple(sk))
        return [list(k) for k in sorted(seen)]


def routes_in_text(text: str) -> list[str]:
    """Route keys of every ``METHOD /path`` in `text` (D4 c). Sentence punctuation after a path
    (``.``, ``:``, ``!``, ``?``) is not part of it."""
    return [route_key(m, p.rstrip(".:!?") or "/") for m, p in _ROUTE.findall(text)]


@dataclass
class Req:
    """A requirement (a rule, a cell, a scenario) and the elements it needs (D4)."""

    id: str
    spec_status: str
    elements: dict[str, dict[str, Any]] = field(default_factory=dict)
    unparsed: list[str] = field(default_factory=list)

    def add(self, key: str, kind: str, via: str, focus: bool = False, **extra: Any) -> None:
        e = self.elements.get(key)
        if e is None:
            e = self.elements[key] = {
                "key": key,
                "kind": kind,
                "via": set(),
                "focus": False,
                **extra,
            }
        e["via"].add(via)
        e["focus"] = e["focus"] or focus


def _scenario_elements(req: Req, sv: SpecView, name: str, conditional: frozenset[str]) -> None:
    keys = [k for k in sv.scenarios.get(name, []) if k not in conditional]
    for i, k in enumerate(keys):
        req.add(f"impl:route:{k}", "route", "scenario", focus=(i == len(keys) - 1))


def rule_requirement(row: Any, sv: SpecView, cv: CodeView) -> Req:
    """E(R) for a rule: its cells' endpoints, every request step of its scenarios (minus
    conditional routes), the routes in its text, its machines, and its tables."""
    req = Req(row.id, row.status)
    for link in row.links:
        kind, _, rest = link.partition(":")
        if kind == "cell":
            for m, p in _ROUTE.findall(rest.split("|", 1)[-1]):
                req.add(f"impl:route:{route_key(m, p)}", "route", "cell")
        elif kind == "scenario":
            _scenario_elements(req, sv, rest, cv.conditional)
        elif kind == "machine":
            t, _, c = rest.partition(".")
            req.add(f"impl:machine:{sv.sql(t)}.{c}", "machine", "machine", spec_table=t, column=c)
        elif kind == "profile":
            t, _, c = rest.partition(".")
            req.add(f"impl:table:{sv.sql(t)}", "table", "profile", table=sv.sql(t))
            if c:
                req.add(
                    f"impl:table:{sv.sql(t)}.{c}", "table", "profile", table=sv.sql(t), column=c
                )
        elif kind == "invariant":
            names, ok = sv.invariant_tables(rest)
            if not ok:
                req.unparsed.append(link)
            for t in names:
                req.add(f"impl:table:{sv.sql(t)}", "table", "invariant", table=sv.sql(t))
        # service: links nothing (services are not extracted)
    for k in routes_in_text(row.text):
        req.add(f"impl:route:{k}", "route", "text")
    return req


def cell_requirement(target: str, key: str) -> Req:
    req = Req(target, "cell")
    req.add(f"impl:route:{key}", "route", "cell", focus=True)
    return req


def scenario_requirement(name: str, sv: SpecView, cv: CodeView) -> Req:
    req = Req(f"scenario:{name}", "scenario")
    _scenario_elements(req, sv, name, cv.conditional)
    return req


# ---- statuses (D5) ------------------------------------------------------------------------


@dataclass
class Facts:
    """What the workflows say, once, for the whole report."""

    cv: CodeView
    verifiable: bool
    blocker: str | None  # why nothing can be verified (a reason), or None
    verified_routes: dict[str, list[str]]  # key -> ids of verified flows
    flows_by_route: dict[str, list[dict[str, Any]]]


def facts_of(cv: CodeView) -> Facts:
    inv = cv.inv
    wf = inv.workflows
    flows_by_route: dict[str, list[dict[str, Any]]] = {}
    if wf is not None:
        for f in wf["flows"]:
            for k in dict.fromkeys(s["route"] for s in f["steps"] if s["kind"] == "request"):
                flows_by_route.setdefault(k, []).append(f)
    blocker: str | None = None
    if inv.stale is not None:
        blocker = "stale"
    elif wf is None:
        blocker = "no verified flow"
    elif inv.mismatch is not None:
        blocker = "workflows_mismatch"
    elif wf["mode"] != "observed":
        blocker = "static run"
    verifiable = blocker is None
    verified_routes: dict[str, list[str]] = {}
    if verifiable:
        for k, fl in flows_by_route.items():
            ids = sorted(f["id"] for f in fl if f["status"] == "verified")
            if ids:
                verified_routes[k] = ids
    return Facts(cv, verifiable, blocker, verified_routes, flows_by_route)


def _element_state(e: dict[str, Any], sv: SpecView, cv: CodeView, facts: Facts) -> dict[str, Any]:
    """Fill present / verified / reason / basis / evidence of one element."""
    kind, key = e["kind"], e["key"]
    present = False
    verified = False
    reason: str | None = None
    evidence: list[str] = []
    if kind == "route":
        rk = key.removeprefix("impl:route:")
        regs = cv.by_key.get(rk, [])
        present = bool(regs)
        for r in regs:
            evidence += route_evidence(cv, r)
        if present:
            if rk in facts.verified_routes:
                verified = True
            elif facts.blocker is not None:
                reason = facts.blocker
            elif any(f["status"] == "failed" for f in facts.flows_by_route.get(rk, [])):
                reason = "flow failed"
            else:
                reason = "no verified flow"
    elif kind == "machine":
        m = cv.machine_for(
            e["spec_table"], key.removeprefix("impl:machine:").split(".")[0], e["column"]
        )
        tbl = key.removeprefix("impl:machine:").split(".")[0]
        if m is not None:
            present = True
            for at in m["evidence"][:3]:
                loc = _parse_at(at)
                if loc is not None:
                    evidence.append(cv.loc(*loc))
            if m["status"] == "confirmed" and facts.verifiable:
                verified = True
            else:
                reason = facts.blocker or f"machine {m['status'] or 'unknown'}"
        elif (tbl, e["column"]) in cv.writes:
            present = True
            for w in cv.inv.extract.get("status_writes") or []:
                if (w.get("table"), w.get("column")) == (tbl, e["column"]):
                    evidence.append(cv.loc(w.get("file"), w.get("line")))
            evidence = sorted(set(evidence))[:3]
            reason = facts.blocker or "static run"
    else:  # table
        t = cv.tables.get(e["table"])
        if t is None:  # the struct name as a second spelling
            t = next((x for x in cv.tables.values() if x.get("struct") == e["table"]), None)
        if t is not None:
            cols = {str(c.get("name")) for c in t.get("columns") or []}
            if e.get("column") is None or e["column"] in cols:
                present = True
                evidence = [cv.loc(t.get("file"), t.get("line"))]
                reason = "static schema"
    e["present"] = present
    e["verified"] = verified
    e["reason"] = None if verified or not present else reason
    e["basis"] = "observed" if verified else ("static" if present else "inferred")
    e["evidence"] = sorted(set(evidence))
    return e


def requirement_status(req: Req, sv: SpecView, cv: CodeView, facts: Facts) -> dict[str, Any]:
    """The report entry of one requirement (D5)."""
    els = [_element_state(e, sv, cv, facts) for _, e in sorted(req.elements.items())]
    for e in els:
        e["via"] = sorted(e["via"])
        if not e["focus"]:
            e.pop("focus")
        for k in ("spec_table", "column", "table"):
            e.pop(k, None)
    missing = [e["key"] for e in els if not e["present"]]
    out: dict[str, Any] = {"id": req.id, "spec_status": req.spec_status}
    if not els:
        status = "no_element"
    elif missing:
        status = "required_not_implemented"
        out["missing"] = missing
        out["partial"] = len(missing) < len(els)
        out["impact_label"] = "not_built_yet"
    else:
        needed = [e for e in els if e["kind"] in ("route", "machine")]
        status = (
            "implemented_verified"
            if needed and all(e["verified"] for e in needed)
            else "implemented_unverified"
        )
    out["status"] = status
    if status == "implemented_unverified":
        pool = [e for e in els if e["kind"] in ("route", "machine")] or els
        out["reasons"] = sorted({e["reason"] for e in pool if e["reason"] and not e["verified"]})
    if req.unparsed:
        out["unparsed_links"] = sorted(req.unparsed)
    out["elements"] = els
    return out


# ---- the report (D6, D7) ------------------------------------------------------------------


def is_plumbing(path: str) -> bool:
    return path in PLUMBING_PATHS or path.startswith(PLUMBING_PREFIX)


def _flow_entry(
    f: Mapping[str, Any],
    cv: CodeView,
    facts: Facts,
    by_element: Mapping[str, list[str]],
    excluded_keys: frozenset[str],
    skeletons: Sequence[Sequence[str]],
) -> dict[str, Any]:
    reqs = [s for s in f["steps"] if s["kind"] == "request"]
    keys = list(dict.fromkeys(s["route"] for s in reqs))
    rules = sorted({r for k in keys for r in by_element.get(f"impl:route:{k}", [])}, key=_natural)
    orphans = [
        k
        for k in keys
        if k not in excluded_keys and f"impl:route:{k}" not in by_element and k in cv.by_key
    ]
    sk = f["skeleton"]
    strict = [i for i, k in enumerate(skeletons) if list(sk) == list(k)]
    lenient = [
        i
        for i, k in enumerate(skeletons)
        if any(list(sk[j:]) == list(k) for j in range(0, f["focus_from"] + 1))
    ]
    effective = f["status"] if f["status"] != "verified" or facts.verifiable else "unverified"
    evidence: set[str] = set()
    for s in reqs:
        for at in s["evidence"]:
            loc = _parse_at(at)
            if loc is not None:
                evidence.add(cv.loc(*loc))
    return {
        "id": f["id"],
        "name": f["name"],
        "inferred_name": f["inferred_name"],
        "kind": f["kind"],
        "status": f["status"],
        "effective": effective,
        "reason": f["reason"] or None,
        "routes": keys,
        "rules": rules,
        "orphan_routes": orphans,
        "implemented_not_required": bool(orphans),
        "spec_matches": {"strict": strict, "lenient": lenient},
        "machine_refs": f["machine_refs"],
        "evidence": sorted(evidence),
        "basis": "observed" if effective == "verified" else "static",
    }


def _natural(rid: str) -> tuple[int, str]:
    m = re.search(r"\d+", rid)
    return (int(m.group()) if m else 0, rid)


def build_report(
    spec: Spec,
    trace: Trace | None,
    inventory: Inventory,
    last_check: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The implementation report, ``lattice.implementation-report/1`` (D7). Pure: the same
    arguments always give the same dict, sorted, with relative ``/`` paths and no time, absolute
    path or response body. Without a trace the requirements are the spec's cells and
    scenarios (``requirement_basis: "spec"``) and there is no ``rules`` section."""
    sv = SpecView.of(spec, trace)
    cv = code_view(inventory)
    facts = facts_of(cv)
    basis = "rules" if trace is not None else "spec"
    reqs: list[Req]
    if basis == "rules":
        reqs = [rule_requirement(row, sv, cv) for row in sv.rule_rows]
    else:
        reqs = [cell_requirement(t, k) for t, k in sorted(sv.cells.items())]
        reqs += [scenario_requirement(n, sv, cv) for n in sorted(sv.scenarios)]
    entries = [requirement_status(r, sv, cv, facts) for r in reqs]
    by_element: dict[str, list[str]] = {}
    for e in entries:
        for el in e["elements"]:
            by_element.setdefault(el["key"], []).append(e["id"])
    by_element = {k: sorted(set(v), key=_natural) for k, v in sorted(by_element.items())}
    if last_check is not None and basis == "rules":
        targets = last_check.get("targets") or {}
        for e in entries:
            v = targets.get(f"rule:{e['id']}")
            e["last_check"] = v if v in ("pass", "fail") else None

    # ---- routes, exclusions and orphans (D6)
    excluded: list[dict[str, Any]] = []
    route_rows: list[dict[str, Any]] = []
    excluded_keys: set[str] = set()
    spec_keys: set[str] = set(sv.cells.values())
    for steps in sv.scenarios.values():
        spec_keys.update(steps)
    for e in entries:
        for el in e["elements"]:
            if el["kind"] == "route" and "text" in el["via"]:
                spec_keys.add(el["key"].removeprefix("impl:route:"))
    for r in cv.routes:
        linked = any(f"impl:route:{k}" in by_element for k in r["keys"])
        in_spec = any(k in spec_keys for k in r["keys"])
        status: str
        reason: str | None = None
        if r["condition"]:
            status, reason = "excluded", "test_only"
        elif not linked and is_plumbing(r["path"]):
            status, reason = "excluded", "plumbing"
        elif linked:
            ver = all(k in facts.verified_routes for k in r["keys"]) if facts.verifiable else False
            status = "implemented_verified" if ver else "implemented_unverified"
        else:
            status = "implemented_not_required"
        if status == "excluded":
            excluded_keys.update(r["keys"])
            excluded.append(
                {
                    "kind": "route",
                    "key": r["id"],
                    "reason": reason,
                    "condition": r["condition"],
                    "evidence": route_evidence(cv, r),
                }
            )
        route_rows.append(
            {
                "key": r["id"],
                "status": status,
                "reason": reason,
                "in_spec": in_spec,
                "rules": sorted(
                    {x for k in r["keys"] for x in by_element.get(f"impl:route:{k}", [])},
                    key=_natural,
                ),
                "auth": r["auth"],
                "statuses": r["status_codes"],
                "refusals": [f"{s} {c}".strip() for s, c in r["refusals"]],
                "handler": r["handler"],
                "collision": r["collision"],
                "via_any": r["via_any"],
                "flows": sorted(
                    {f["id"] for k in r["keys"] for f in facts.flows_by_route.get(k, [])}
                ),
                "evidence": route_evidence(cv, r),
            }
        )
    for item in inventory.extract.get("excluded") or []:
        excluded.append(
            {
                "kind": str(item.get("kind") or "framework"),
                "key": f"{item.get('method')} {item.get('path')}",
                "reason": str(item.get("reason") or "framework"),
                "evidence": [cv.loc(item.get("file"), item.get("line"))],
            }
        )
    excluded.sort(key=lambda x: (x["kind"], x["key"], x["reason"]))
    orphans = [r["key"] for r in route_rows if r["status"] == "implemented_not_required"]

    # ---- endpoints: spec and code together
    endpoints: list[dict[str, Any]] = []
    code_keys = set(cv.by_key)
    by_route_status = {r["key"]: r for r in route_rows}
    for k in sorted(spec_keys | code_keys):
        regs = cv.by_key.get(k, [])
        in_spec = k in spec_keys
        if not regs:
            st = "spec_only"
        else:
            row = by_route_status[regs[0]["id"]]
            if row["status"] == "excluded":
                st = "test_only" if row["reason"] == "test_only" else "plumbing"
            elif row["status"] == "implemented_not_required":
                st = "implemented_not_required"
            else:
                st = "implemented"
        endpoints.append({"key": k, "status": st, "in_spec": in_spec, "in_code": bool(regs)})

    # ---- flows
    skeletons = sv.skeletons(cv.conditional)
    flows = [
        _flow_entry(f, cv, facts, by_element, frozenset(excluded_keys), skeletons)
        for f in (inventory.workflows["flows"] if inventory.workflows else [])
    ]
    # ---- machines and tables
    machine_rows: list[dict[str, Any]] = []
    for m in cv.machines:
        key = f"impl:machine:{m['table']}.{m['column']}"
        rules = by_element.get(key, [])
        machine_rows.append(
            {
                "key": key,
                "id": m["id"],
                "states": m["states"],
                "edges": [f"{e['from']} -> {e['to']}" for e in m["edges"]],
                "status": m["status"],
                "rules": rules,
                "implemented_not_required": not rules,
                "evidence": [cv.loc(*loc) for a in m["evidence"] if (loc := _parse_at(a))],
            }
        )
    table_rows = [
        {
            "key": f"impl:table:{name}",
            "struct": t.get("struct"),
            "rules": by_element.get(f"impl:table:{name}", []),
            "evidence": [cv.loc(t.get("file"), t.get("line"))],
        }
        for name, t in sorted(cv.tables.items())
    ]
    counts: dict[str, int] = dict.fromkeys(STATUSES, 0)
    for e in entries:
        counts[e["status"]] += 1
    summary = {
        "requirements": counts,
        "routes": _count(route_rows, "status"),
        "endpoints": _count(endpoints, "status"),
        "flows": _count(flows, "effective"),
        "orphan_routes": len(orphans),
        "orphan_flows": sum(1 for f in flows if f["implemented_not_required"]),
        "orphan_machines": sum(1 for m in machine_rows if m["implemented_not_required"]),
        "excluded": len(excluded),
    }
    unread = sorted(
        (
            {
                "file": u.get("file"),
                "line": u.get("line"),
                "what": u.get("what"),
                "reason": u.get("reason"),
            }
            for u in inventory.extract.get("unread") or []
        ),
        key=lambda u: (str(u["file"]), int(u["line"] or 0), str(u["what"]), str(u["reason"])),
    )
    report: dict[str, Any] = {
        "schema": SCHEMA_REPORT,
        "requirement_basis": basis,
        "inventory": {
            "extract_sha256": inventory.extract_sha256,
            "workflows_sha256": inventory.workflows_sha256,
            "run_mode": inventory.run_mode,
            "stale": inventory.stale,
            "workflows_mismatch": inventory.mismatch,
            "workflows_consistency": inventory.consistency,
        },
        "summary": summary,
        ("rules" if basis == "rules" else "requirements"): entries,
        "endpoints": endpoints,
        "routes": route_rows,
        "flows": flows,
        "machines": machine_rows,
        "tables": table_rows,
        "implemented_not_required": [{"kind": "route", "key": k} for k in orphans]
        + [
            {"kind": "flow", "key": f["id"], "routes": f["orphan_routes"]}
            for f in flows
            if f["implemented_not_required"]
        ]
        + [
            {"kind": "machine", "key": m["key"]}
            for m in machine_rows
            if m["implemented_not_required"]
        ],
        "excluded": excluded,
        "spec_skeletons": skeletons,
        "by_element": by_element,
        "unread": unread,
    }
    return report


def _count(rows: Sequence[Mapping[str, Any]], field_: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r[field_])] = out.get(str(r[field_]), 0) + 1
    return dict(sorted(out.items()))


def report_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def render(report: Mapping[str, Any], *, rule: str | None = None, status: str | None = None) -> str:
    """The report as text, one evidence line per item."""
    inv = report["inventory"]
    out = [
        f"implementation report ({report['requirement_basis']} basis)",
        f"inventory: extract {inv['extract_sha256'][:12]}, workflows "
        f"{(inv['workflows_sha256'] or 'none')[:12]}, run {inv['run_mode'] or 'none'}",
    ]
    if inv["stale"]:
        c = inv["stale"]["counts"]
        out.append(
            f"STALE: the source changed since the inventory ({c['changed']} changed, "
            f"{c['added']} added, {c['removed']} removed): nothing is verified"
        )
    if inv["workflows_mismatch"]:
        out.append(f"WORKFLOWS MISMATCH: {inv['workflows_mismatch']}")
    s = report["summary"]
    out.append("requirements: " + ", ".join(f"{k} {v}" for k, v in s["requirements"].items()))
    out.append("routes: " + ", ".join(f"{k} {v}" for k, v in s["routes"].items()))
    out.append("endpoints: " + ", ".join(f"{k} {v}" for k, v in s["endpoints"].items()))
    out.append("flows: " + (", ".join(f"{k} {v}" for k, v in s["flows"].items()) or "none"))
    out.append("")
    section = "rules" if "rules" in report else "requirements"
    for e in report[section]:
        if rule and e["id"] != rule:
            continue
        if status and e["status"] != status:
            continue
        extra = ""
        if e["status"] == "required_not_implemented":
            extra = f" missing {', '.join(m.removeprefix('impl:') for m in e['missing'])}" + (
                " (partial)" if e["partial"] else ""
            )
        elif e.get("reasons"):
            extra = f" ({'; '.join(e['reasons'])})"
        out.append(f"{e['id']} [{e['spec_status']}] {e['status']}{extra}")
        for el in e["elements"][:12]:
            ev = el["evidence"][0] if el["evidence"] else "-"
            mark = "verified" if el["verified"] else ("present" if el["present"] else "absent")
            out.append(f"    {el['key'].removeprefix('impl:')} {mark} {el['basis']} {ev}")
    if not rule and not status:
        out += ["", "implemented, required by no rule:"]
        out += [f"    {x['kind']} {x['key']}" for x in report["implemented_not_required"]] or [
            "    (none)"
        ]
        out += ["", "excluded:"]
        out += [
            f"    {x['kind']} {x['key']}: {x['reason']} {x['evidence'][0]}"
            for x in report["excluded"]
        ] or ["    (none)"]
    return "\n".join(out) + "\n"


# ---- the code: citation checker (D8) -------------------------------------------------------


def evidence_symbol(
    out: dict[str, set[str]], inv: Inventory, file: Any, line: Any, symbol: str
) -> None:
    out.setdefault(f"{inv.evidence_path(str(file))}:{int(line or 0)}", set()).add(symbol)


def evidence_locations(inv: Inventory) -> dict[str, set[str]]:
    """{``path:line``: symbols the inventory names there} for every evidence location of the
    elements (D3): route registrations and handlers, step evidence, machine edges, structs."""
    cv = code_view(inv)
    out: dict[str, set[str]] = {}

    def add(file: Any, line: Any, symbol: str | None = None) -> None:
        loc = f"{inv.evidence_path(str(file))}:{int(line or 0)}"
        out.setdefault(loc, set())
        if symbol:
            out[loc].add(symbol)

    for r in cv.routes:
        add(r["file"], r["line"])
        if r.get("handler_file"):
            add(r["handler_file"], r["handler_line"])
            for sym in str(r["handler"] or "").split("."):  # api.Login.func1: every component
                if sym:
                    evidence_symbol(out, inv, r["handler_file"], r["handler_line"], sym)
    for t in cv.tables.values():
        add(t.get("file"), t.get("line"), t.get("struct"))
    if inv.workflows:
        for f in inv.workflows["flows"]:
            for s in f["steps"]:
                for at in s.get("evidence") or []:
                    loc = _parse_at(at)
                    if loc is not None:
                        add(*loc)
        for m in inv.workflows["machines"]:
            for at in m["evidence"]:
                loc = _parse_at(at)
                if loc is not None:
                    add(*loc)
    return out


def _words(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))


def parse_code_target(target: str) -> tuple[str, int] | None:
    """(relative path, line) of ``code:<relpath>:<line>`` (exactly one trailing ``:<int>``)."""
    m = _CODE_TARGET.match(target)
    if m is None or ":" in m.group(1):
        return None
    return m.group(1), int(m.group(2))


def check_code(
    app_root: Path | str, inventory: Inventory | None, citation: Mapping[str, Any]
) -> str | None:
    """Why a ``code:<relpath>:<line>`` citation does not hold (None: it does). Deterministic;
    it only reads files. Holds iff: the path is relative and normalised; its realpath is under
    `app_root` with no symlink component; each component matches the directory listing
    case-exactly; the file is in the inventory's source set, or is a code file outside the
    skipped directories, and is at most 1 MB; the line exists; the quote (at least 8 non-space
    characters, whitespace-normalised) is a substring of that one line; a given symbol is a
    whole word on that line, or the inventory's symbol there."""
    target = str(citation.get("target") or "")
    parsed = parse_code_target(target)
    if parsed is None:
        return "a code citation is code:<relative path>:<line> with exactly one trailing :<int>"
    rel, line_no = parsed
    if (
        not rel
        or rel.startswith("/")
        or "\\" in rel
        or _CONTROL.search(rel)
        or any(c in ("", ".", "..") for c in rel.split("/"))
    ):
        return f"the path {rel!r} is not a relative, normalised path"
    root = Path(app_root)
    try:
        real_root = root.resolve()
    except OSError:
        return "the app root cannot be read"
    cur = root
    for comp in rel.split("/"):
        try:
            names = os.listdir(cur)
        except OSError:
            return f"{rel!r}: no such directory or file"
        if comp not in names:
            hint = (
                "differs in case"
                if comp.lower() in {n.lower() for n in names}
                else "does not exist"
            )
            return f"{rel!r}: {comp!r} {hint}"
        cur = cur / comp
        if cur.is_symlink():
            return f"{rel!r}: {comp!r} is a symbolic link"
    try:
        real = cur.resolve()
    except OSError:
        return f"{rel!r} cannot be resolved"
    if real != real_root and real_root not in real.parents:
        return f"{rel!r} is outside the app root"
    if not cur.is_file():
        return f"{rel!r} is not a file"
    skip, code_ext = _tools_consts()
    in_set = inventory is not None and rel in inventory.source_paths
    if not in_set:
        parts = rel.split("/")
        if Path(rel).suffix not in code_ext or any(p in skip for p in parts[:-1]):
            return f"{rel!r} is not in the source set and is not a code file"
    try:
        size = cur.stat().st_size
    except OSError:
        return f"{rel!r} cannot be read"
    if size > MAX_CODE_FILE:
        return f"{rel!r} is larger than 1 MB"
    text = cur.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not 1 <= line_no <= len(lines):
        return f"{rel!r} has {len(lines)} lines, not line {line_no}"
    line = lines[line_no - 1]
    quote = str(citation.get("quote") or "")
    if len("".join(quote.split())) < MIN_QUOTE:
        return f"the quote has fewer than {MIN_QUOTE} non-space characters"
    if " ".join(quote.split()) not in " ".join(line.split()):
        return f"the quote is not in {rel}:{line_no}"
    symbol = citation.get("symbol")
    if symbol:
        sym = str(symbol)
        known = set()
        if inventory is not None:
            known = evidence_locations(inventory).get(f"{rel}:{line_no}", set())
        if sym not in _words(line) and sym not in known:
            return f"the symbol {sym!r} is not on {rel}:{line_no}"
    return None


# ---- the context the ask path uses (D9) ---------------------------------------------------

IMPLEMENTATION_LABELS = (
    "implemented_verified",
    "implemented_unverified",
    "required_not_implemented",
    "not_specified_not_implemented",
    "ambiguous",
    "unsettled",
)
IMPLEMENTATION_BASES = ("observed", "static", "inferred")


@dataclass
class ImplContext:
    """An inventory, its report and what the citation checks need, built once per digest."""

    inv: Inventory
    report: dict[str, Any]
    cv: CodeView
    sv: SpecView
    evidence: dict[str, set[str]]
    flows: dict[str, dict[str, Any]]

    @classmethod
    def of(cls, spec: Spec, trace: Trace | None, inv: Inventory) -> ImplContext:
        report = build_report(spec, trace, inv)
        sv = SpecView.of(spec, trace)
        cv = code_view(inv)
        flows = {f["id"]: f for f in (inv.workflows["flows"] if inv.workflows else [])}
        return cls(inv, report, cv, sv, evidence_locations(inv), flows)

    @property
    def app_root(self) -> Path:
        return self.inv.app_root

    @property
    def stale(self) -> bool:
        return self.inv.stale is not None

    def requirement(self, rid: str) -> dict[str, Any] | None:
        section = "rules" if "rules" in self.report else "requirements"
        return next((e for e in self.report[section] if e["id"] == rid), None)

    def flow_status(self, fid: str) -> str | None:
        """The effective status of a flow: ``verified`` only when the inventory is fresh,
        consistent and observed."""
        row = next((f for f in self.report["flows"] if f["id"] == fid), None)
        return row["effective"] if row else None

    def check_code(self, citation: Mapping[str, Any]) -> str | None:
        return check_code(self.inv.app_root, self.inv, citation)

    def is_evidence(self, target: str) -> bool:
        parsed = parse_code_target(target)
        return parsed is not None and f"{parsed[0]}:{parsed[1]}" in self.evidence

    def target_endpoints(self, target: str) -> set[str]:
        """The route keys a ``rule:``, ``cell:`` or ``scenario:`` target needs (D4)."""
        kind, _, rest = target.partition(":")
        if kind == "cell":
            k = self.sv.cells.get(target)
            return {k} if k else set()
        if kind == "scenario":
            return {k for k in self.sv.scenarios.get(rest, []) if k not in self.cv.conditional}
        if kind == "rule":
            e = self.requirement(rest)
            return {
                x["key"].removeprefix("impl:route:")
                for x in (e or {}).get("elements", [])
                if x["kind"] == "route"
            }
        return set()

    def missing_endpoints(self) -> set[str]:
        out = {e["key"] for e in self.report["endpoints"] if e["status"] == "spec_only"}
        section = "rules" if "rules" in self.report else "requirements"
        for e in self.report[section]:
            out.update(
                m.removeprefix("impl:route:")
                for m in e.get("missing", [])
                if m.startswith("impl:route:")
            )
        return out


def settle_implementation(
    label: str | None, cites: Sequence[Mapping[str, Any]], ctx: ImplContext
) -> tuple[str | None, str | None]:
    """The deterministic downgrades of an answer's ``implementation`` label (D9), from its
    VALID citations; a label is never upgraded. Returns (label, basis)."""
    if label is None:
        return None, None
    targets = [str(c.get("target") or "") for c in cites]
    evidence_code = [t for t in targets if t.startswith("code:") and ctx.is_evidence(t)]
    elements = [t for t in targets if t.startswith(("impl:route:", "impl:flow:", "impl:machine:"))]
    out = label
    if out == "implemented_verified":
        verified_flow = any(
            t.startswith("impl:flow:")
            and ctx.flow_status(t.removeprefix("impl:flow:")) == "verified"
            for t in targets
        )
        verified_rule = any(
            t.startswith("impl:rule:")
            and (ctx.requirement(t.removeprefix("impl:rule:")) or {}).get("status")
            == "implemented_verified"
            for t in targets
        )
        if ctx.stale or not ((verified_flow or verified_rule) and evidence_code):
            out = "implemented_unverified"
    if out == "implemented_unverified" and not (evidence_code or elements):
        out = "unsettled"
    if out == "required_not_implemented":
        missing = ctx.missing_endpoints()
        ok = any(
            t.startswith(("rule:", "cell:", "scenario:")) and ctx.target_endpoints(t) & missing
            for t in targets
        )
        if not ok:
            out = "unsettled"
    if out == "ambiguous" and len(set(targets)) < 2:
        out = "unsettled"
    basis = {
        "implemented_verified": "observed",
        "implemented_unverified": "static",
        "required_not_implemented": "inferred",
        "not_specified_not_implemented": "inferred",
        "ambiguous": "static",
    }.get(out)
    return out, basis


def flow_steps(flow: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A flow's steps for replay: requests ``{method, path, user, json, expect_status}`` and
    awaits ``{service, expect_db, within_s}``, in order."""
    out: list[dict[str, Any]] = []
    for s in flow["steps"]:
        if s["kind"] == "await":
            out.append(
                {"service": s["service"], "expect_db": s["expect_db"], "within_s": s["within_s"]}
            )
        else:
            out.append(
                {
                    "method": s["method"],
                    "path": s["path"],
                    "user": s["caller"],
                    "json": s["json"],
                    "expect_status": s["expect_status"],
                }
            )
    return out


def flow_scenario(flow: Mapping[str, Any]) -> Any:
    """The :class:`~lattice.spec.model.Scenario` that replays a flow: ``Step`` per request and
    ``Await`` per await step (no model call)."""
    from lattice.spec.model import Scenario

    steps: list[Any] = []
    for s in flow_steps(flow):
        if "service" in s:
            steps.append(Await(s["service"], s["expect_db"], within_s=float(s["within_s"] or 10.0)))
        else:
            steps.append(
                Step(
                    s["method"],
                    s["path"],
                    s["user"],
                    json=s["json"],
                    expect_status=int(s["expect_status"]),
                )
            )
    return Scenario(f"flow_{flow['id']}", steps=tuple(steps))


# ---- findings, lookup and find (D10) ------------------------------------------------------

FIND_THRESHOLD = 0.15  # no_match: the top score is under this share of the best possible
FIND_TIE = 0.10  # several: the second is within this share of the top
_KIND_RANK = {"route": 0, "machine": 1, "flow": 2, "rule": 3}


def _sid(text: str, n: int = 200) -> str:
    return text if len(text) <= n else text[: n - 3] + "..."


def findings(ctx: ImplContext) -> list[dict[str, Any]]:
    """The report's findings, each with a ``proposal`` (text only, never sent from here): a
    rule the code does not implement, a route / flow / machine no rule requires."""
    rep = ctx.report
    out: list[dict[str, Any]] = []
    section = "rules" if "rules" in rep else "requirements"
    for e in rep[section]:
        if e["status"] != "required_not_implemented":
            continue
        miss = ", ".join(m.removeprefix("impl:") for m in e["missing"])
        title = f"{e['id']} is required but not implemented: {miss}"
        out.append(
            {
                "id": f"required_not_implemented:{e['id']}",
                "kind": "required_not_implemented",
                "target": e["id"],
                "title": _sid(title, 300),
                "basis": "inferred",
                "partial": e["partial"],
                "proposal": {
                    "request": (
                        f"The app does not implement what {e['id']} requires (missing: {miss}). "
                        "Reconsider the requirement: keep it as it is, reword it, or drop it."
                    ),
                    "selected": [f"rule:{e['id']}"] if section == "rules" else [e["id"]],
                    "source": {
                        "server": "extractor",
                        "item": _sid(f"required_not_implemented:{e['id']}"),
                        "title": _sid(title, 300),
                    },
                },
            }
        )
    for o in rep["implemented_not_required"]:
        if o["kind"] != "route":
            continue
        row = next(r for r in rep["routes"] if r["key"] == o["key"])
        title = f"{o['key']} is implemented but no requirement mentions it"
        out.append(
            {
                "id": f"implemented_not_required:{o['key']}",
                "kind": "implemented_not_required",
                "target": o["key"],
                "title": _sid(title, 300),
                "basis": "static",
                "evidence": row["evidence"],
                "proposal": {
                    "request": (
                        f"The app implements {o['key']} ({row['evidence'][0]}), but no requirement "
                        "mentions it. Add a requirement that describes it, or leave it "
                        "undocumented."
                    ),
                    "selected": [],
                    "source": {
                        "server": "extractor",
                        "item": _sid(f"implemented_not_required:{o['key']}"),
                        "title": _sid(title, 300),
                    },
                },
            }
        )
    return out


def _find_docs(ctx: ImplContext) -> dict[str, dict[str, Any]]:
    """The searchable documents: target -> {kind, status, text, keys, evidence}."""
    rep = ctx.report
    texts = {r.id: r.text for r in ctx.sv.rule_rows}
    docs: dict[str, dict[str, Any]] = {}
    flows_of_route: dict[str, list[str]] = {}
    for f in rep["flows"]:
        for k in f["routes"]:
            flows_of_route.setdefault(k, []).append(f["name"])
    for r in rep["routes"]:
        extra = [texts.get(x, x) for x in r["rules"]] + flows_of_route.get(r["key"], [])
        docs[f"impl:route:{r['key']}"] = {
            "kind": "route",
            "status": r["status"],
            "text": " ".join([r["key"], str(r["handler"] or ""), *extra]),
            "keys": [r["key"]],
            "evidence": r["evidence"],
        }
    section = "rules" if "rules" in rep else "requirements"
    for e in rep["endpoints"]:
        if e["status"] != "spec_only":
            continue
        refs = [
            x
            for x in rep[section]
            if f"impl:route:{e['key']}" in {el["key"] for el in x["elements"]}
        ]
        docs[f"impl:route:{e['key']}"] = {
            "kind": "route",
            "status": "spec_only",
            "text": " ".join([e["key"], *[texts.get(x["id"], x["id"]) for x in refs]]),
            "keys": [e["key"]],
            "evidence": [],
        }
    for f in rep["flows"]:
        docs[f"impl:flow:{f['id']}"] = {
            "kind": "flow",
            "status": f["effective"],
            "text": " ".join([f["name"], f["kind"], *f["routes"]]),
            "keys": f["routes"],
            "evidence": f["evidence"][:3],
        }
    for m in rep["machines"]:
        docs[m["key"]] = {
            "kind": "machine",
            "status": m["status"],
            "text": " ".join([m["key"], *m["states"], *[texts.get(x, x) for x in m["rules"]]]),
            "keys": [m["key"]],
            "evidence": m["evidence"][:3],
        }
    for e in rep[section]:
        if e["status"] == "required_not_implemented":
            docs[f"rule:{e['id']}"] = {
                "kind": "rule",
                "status": "required_not_implemented",
                "text": " ".join([texts.get(e["id"], e["id"]), *e["missing"]]),
                "keys": [m.removeprefix("impl:route:") for m in e["missing"]],
                "evidence": [],
            }
    return docs


def find_candidates(ctx: ImplContext, question: str, limit: int = 5) -> dict[str, Any]:
    """Deterministic, lexical: exact templated ``METHOD /path`` mentions first, then the
    IDF-weighted overlap of ``ask.terms`` with each element's document. Not understanding."""
    import math

    from lattice.spec.ask import terms

    docs = _find_docs(ctx)
    n = max(len(docs), 1)
    doc_terms = {t: terms(d["text"]) for t, d in docs.items()}
    df: dict[str, int] = {}
    for ts in doc_terms.values():
        for w in ts:
            df[w] = df.get(w, 0) + 1
    idf = {w: math.log(1 + n / c) for w, c in df.items()}
    query = terms(question)
    mentioned = set(routes_in_text(question))
    best_possible = sum(idf.get(w, 0.0) for w in query)
    rows = []
    for t, d in docs.items():
        hit = sorted(query & doc_terms[t])
        score = sum(idf.get(w, 0.0) for w in hit)
        exact = bool(mentioned & set(d["keys"])) and d["kind"] == "route"
        rows.append((exact, score, t, d, hit))
    rows = [r for r in rows if r[0] or r[1] > 0]
    rows.sort(key=lambda r: (not r[0], -r[1], _KIND_RANK[r[3]["kind"]], r[2]))
    cands = [
        {
            "target": t,
            "kind": d["kind"],
            "status": d["status"],
            "score": round(score, 4),
            "matched_terms": hit,
            "exact": exact,
            "evidence": d["evidence"][:3],
        }
        for exact, score, t, d, hit in rows[:limit]
    ]
    if not rows:
        hint = "no_match"
    elif rows[0][0]:
        hint = "spec_only" if rows[0][3]["status"] == "spec_only" else "exists"
    elif best_possible <= 0 or rows[0][1] < FIND_THRESHOLD * best_possible:
        hint = "no_match"
    elif len(rows) > 1 and rows[1][1] >= (1 - FIND_TIE) * rows[0][1]:
        hint = "several"
    else:
        top = rows[0][3]
        hint = (
            "spec_only" if top["status"] in ("spec_only", "required_not_implemented") else "exists"
        )
    return {
        "question": question,
        "hint": hint,
        "candidates": cands,
        "note": "lexical match, not understanding: terms overlap with the keys, handlers, flow "
        "names and the texts of the rules that link each element",
    }


def get_workflow(ctx: ImplContext, ident: str) -> dict[str, Any]:
    """One flow or machine: steps, status, evidence, rules, spec matches. An unknown id raises
    :class:`ImplementationError` ``unknown_id`` naming the 5 nearest ids."""
    import difflib

    ident = ident.strip().removeprefix("impl:flow:").removeprefix("impl:machine:")
    rep = ctx.report
    flows = {f["id"]: f for f in rep["flows"]}
    if ident in flows:
        row = flows[ident]
        raw = ctx.flows[ident]
        return {
            "type": "flow",
            **row,
            "steps": flow_steps(raw),
            "step_evidence": {
                f"{s['i']}": s["evidence"] for s in raw["steps"] if s["kind"] == "request"
            },
            "spec_skeletons": {
                "strict": [rep["spec_skeletons"][i] for i in row["spec_matches"]["strict"]],
                "lenient": [rep["spec_skeletons"][i] for i in row["spec_matches"]["lenient"]],
            },
            "basis": row["basis"],
        }
    machines = {m["id"]: m for m in rep["machines"]}
    by_key = {m["key"].removeprefix("impl:machine:"): m for m in rep["machines"]}
    m = machines.get(ident) or by_key.get(ident)
    if m is not None:
        return {
            "type": "machine",
            **m,
            "basis": "observed" if m["status"] in ("confirmed", "observed") else "static",
        }
    pool = [*flows, *machines, *by_key]
    near = difflib.get_close_matches(ident, pool, n=5, cutoff=0.0)
    raise ImplementationError(
        "unknown_id", f"no flow or machine {ident!r}; nearest: {', '.join(near)}"
    )


# ---- the CLI ------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m lattice.spec implementation",
        description="What the code implements against what the spec requires (read-only).",
    )
    p.add_argument("--spec", required=True, help="module:SPEC (a Spec, or a function)")
    p.add_argument(
        "--trace", type=Path, help="the spec's trace JSON (without one: cells/scenarios)"
    )
    p.add_argument("--inventory", type=Path, required=True, help="the inventory directory")
    p.add_argument("--app-root", type=Path, default=Path("."), help="the app's root (default .)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--rule", help="only this rule or requirement id")
    p.add_argument("--status", choices=STATUSES, help="only this status")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0: the report was produced; 2: it could not be. Writes nothing."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "implementation":
        argv = argv[1:]
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        from lattice.spec.__main__ import load

        spec = load(args.spec)
        if not isinstance(spec, Spec) and callable(spec):
            spec = spec()
        if not isinstance(spec, Spec):
            raise ValueError(f"{args.spec} is not a Spec")
        trace = Trace.load(args.trace) if args.trace else None
        inv = load_inventory(args.inventory, args.app_root)
        report = build_report(spec, trace, inv)
    except ImplementationError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except Exception as err:  # noqa: BLE001 -- any failure to report is exit 2
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    if args.json:
        if args.rule or args.status:
            section = "rules" if "rules" in report else "requirements"
            report = dict(report)
            report[section] = [
                e
                for e in report[section]
                if (not args.rule or e["id"] == args.rule)
                and (not args.status or e["status"] == args.status)
            ]
        sys.stdout.write(report_json(report))
    else:
        sys.stdout.write(render(report, rule=args.rule, status=args.status))
    return 0


__all__ = [
    "IMPLEMENTATION_BASES",
    "IMPLEMENTATION_LABELS",
    "ImplContext",
    "ImplementationError",
    "Inventory",
    "SpecView",
    "build_report",
    "check_code",
    "implementation_config",
    "load_inventory",
    "render",
    "report_json",
    "find_candidates",
    "findings",
    "flow_scenario",
    "get_workflow",
    "route_key",
    "settle_implementation",
    "source_files",
    "source_hashes",
    "template_path",
]

if __name__ == "__main__":
    sys.exit(main())
