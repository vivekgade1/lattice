"""`lattice.spec.appmap`: the app map, ``lattice.appmap/1`` (docs/results/2026-10-16-goto-place-
prereg.md, D1-D2; CLI ``python -m lattice.spec appmap``).

What does the code implement of what the spec requires, and where is the evidence? The map is a
**pure join of reports that already exist**: Spike C's :func:`lattice.spec.implementation.
build_report` (rules, routes, endpoints, flows, machines, exclusions, unread) and
:mod:`lattice.spec.review`'s ``ReviewData`` (rule text, spec status, the access matrix), plus the
request steps of the inventory's workflows (for each step's caller). It extracts nothing: it runs
no extractor, ``go``, git, Docker or model, reads no source file (the inventory loader re-hashes
the source set, as it always does), and infers nothing from a name. Every value is copied from the
report that holds it; a join is an exact-key lookup (routes on their template form, ``{id}`` and
``{habit_id}`` both ``{}``).

**The map never decides agreement.** It does not compare a spec cell with the code's ``auth``,
nor a spec machine with the code's edges beyond set membership. Whether the code honours a cell is
layer D's job. A cell shows the spec's value, the code's facts and the answers observed flows got,
side by side. Basis words are fixed: ``observed`` (a Spike B replay of those requests passed on a
fresh database at capture time), ``static`` (read from the code), ``inferred`` (an absence in a
static extract), ``none`` (a rule with no code element). A stale inventory turns
``implemented_verified`` into ``implemented_unverified`` (Spike C's rule, inherited).

The output is canonical: sorted keys (when serialised with :func:`map_json`), sorted arrays,
relative ``/`` paths, no time, host, port or absolute path.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.spec import implementation as impl
from lattice.spec.model import Spec
from lattice.spec.review import ReviewData, _build
from lattice.spec.rules import Trace

SCHEMA = "lattice.appmap/1"
SECTIONS = ("summary", "rules", "workflows", "machines", "access", "endpoints", "unread")
STATES = ("ok", "not_configured", "inventory_missing", "bad_inventory", "no_spec")
BASIS_OF = {
    "implemented_verified": "observed",
    "implemented_unverified": "static",
    "required_not_implemented": "inferred",
    "no_element": "none",
}
MEANING = "these elements reference it; whether they fail is not decided"
NOTES = {
    "no_workflows_capture": (
        "machines and flows come from a workflows capture; this inventory has none"
    ),
    "static_capture": (
        "the workflows capture is static: flows and machines are read from the code, not replayed"
    ),
    "stale": ("the code changed since the inventory was captured; nothing is shown as observed"),
    "no_trace": "no trace was given; there are no rules to list",
}
NEAREST = 5
MAX_KEY = 300
_AT = re.compile(r"^(.+):(\d+)$")
_PATH = re.compile(r"/[^\s]*")

_SPEC_FILE, _TRACE_FILE, _MD_FILE = "spec.py", "spec.trace.json", "REQUIREMENTS.md"


class ElementError(Exception):
    """``code``: ``unknown_key`` (with ``nearest``) or ``bad_key``."""

    def __init__(self, code: str, detail: str, nearest: Sequence[str] = ()) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.nearest = list(nearest)


# ---- small helpers -------------------------------------------------------------------------------


def _sha_bytes(raw: bytes | None) -> str | None:
    return None if raw is None else hashlib.sha256(raw).hexdigest()


def _sha_text(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def map_json(doc: Mapping[str, Any]) -> str:
    """The map as the CLI prints it: sorted keys, indent 1, a final newline."""
    return json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def sources_of(
    *, spec: str | bytes | None, trace: str | bytes | None, requirements: str | bytes | None
) -> dict[str, str | None]:
    """The ``inputs`` hashes of the three spec files (``None`` for a file that is absent)."""

    def h(v: str | bytes | None) -> str | None:
        if v is None:
            return None
        return _sha_bytes(v if isinstance(v, bytes) else v.encode("utf-8"))

    return {
        "spec_sha256": h(spec),
        "trace_sha256": h(trace),
        "requirements_sha256": h(requirements),
    }


def last_check_input(last_check: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if last_check is None:
        return None
    return {"verdict": last_check.get("verdict"), "sha256": _sha_text(canonical(last_check))}


def _inventory_input(inv: impl.Inventory | None) -> dict[str, Any] | None:
    if inv is None:
        return None
    return {
        "extract_sha256": inv.extract_sha256,
        "workflows_sha256": inv.workflows_sha256,
        "run_mode": inv.run_mode,
        "stale": inv.stale,
    }


def _natural(rid: str) -> tuple[int, str]:
    m = re.search(r"\d+", rid)
    return (int(m.group()) if m else 0, rid)


def _count(values: Sequence[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))


def _at(cv: impl.CodeView, value: Any) -> str | None:
    """``code:<path>:<line>`` of an extract location ``path:line`` (None if it is not one)."""
    m = _AT.match(str(value))
    return cv.loc(m.group(1), int(m.group(2))) if m else None


def _cell_route_key(col: str) -> str:
    method, _, path = col.partition(" ")
    return impl.route_key(method, path)


def _scrub(message: str) -> str:
    """A message without an absolute path (the map holds none)."""
    return _PATH.sub("<path>", message) if "/" in message else message


# ---- the map -------------------------------------------------------------------------------------


@dataclass
class AppMap:
    """The map (``doc``), the Spike C report it was joined from and the lookup tables."""

    doc: dict[str, Any]
    report: dict[str, Any] = field(default_factory=dict)
    _index: dict[str, tuple[str, Any]] | None = None

    @property
    def state(self) -> str:
        return str(self.doc["state"])


def non_ok(state: str, why: str, inputs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The map of a state other than ``ok``: only ``schema``, ``state``, ``inputs``, ``why``."""
    assert state in STATES and state != "ok"
    inp = dict(inputs or {})
    inp.setdefault("spec_sha256", None)
    inp.setdefault("trace_sha256", None)
    inp.setdefault("requirements_sha256", None)
    inp.setdefault("inventory", None)
    inp.setdefault("last_check", None)
    return {"schema": SCHEMA, "state": state, "inputs": inp, "why": _scrub(why)}


def _spec_machine_rows(
    spec: Spec, sv: impl.SpecView, cv: impl.CodeView
) -> dict[str, tuple[str, set[tuple[str, str]]]]:
    """machine row key -> (spec target ``machine:T.c``, the spec machine's moves), by the same
    ``machine_for`` match Spike C uses (``spec_object``, else the SQL name)."""
    out: dict[str, tuple[str, set[tuple[str, str]]]] = {}
    for sm in spec.machines:
        m = cv.machine_for(sm.table, sv.sql(sm.table), sm.column)
        if m is None:
            continue
        key = f"impl:machine:{m['table']}.{m['column']}"
        out.setdefault(key, (f"machine:{sm.table}.{sm.column}", {(a, b) for a, b in sm.edges}))
    return out


def build(
    spec: Spec,
    trace: Trace | None,
    inventory: impl.Inventory,
    review: ReviewData | None = None,
    last_check: Mapping[str, Any] | None = None,
    sources: Mapping[str, str | None] | None = None,
) -> AppMap:
    """The map and what it was joined from. `review` is built when not given; `sources` are the
    ``inputs`` hashes of the three spec files (see :func:`sources_of`)."""
    tr = trace if trace is not None else Trace(source="", rules=[])
    if review is None:
        review = _build(spec, tr, None, None, "app map")
    report = impl.build_report(spec, tr, inventory, last_check)
    sv = impl.SpecView.of(spec, tr)
    cv = impl.code_view(inventory)
    text_of = {r.id: r for r in review.rule_rows}
    flows_by_id = {f["id"]: f for f in (inventory.workflows or {}).get("flows", [])}
    stale = inventory.stale is not None

    # ---- machines (the report's rows, plus the spec side by set membership)
    spec_rows = _spec_machine_rows(spec, sv, cv)
    machines: list[dict[str, Any]] = []
    for row in sorted(report["machines"], key=lambda m: m["key"]):
        target, moves = spec_rows.get(row["key"], (None, set()))
        code_edges = {tuple(e.split(" -> ", 1)) for e in row["edges"]}
        both = sorted(moves | code_edges)
        machines.append(
            {
                **row,
                "spec_target": target,
                "edges_detail": [
                    {
                        "from": a,
                        "to": b,
                        "in_spec": (a, b) in moves,
                        "in_code": (a, b) in code_edges,
                    }
                    for a, b in both
                ],
            }
        )

    # ---- workflows
    workflows: list[dict[str, Any]] = []
    for f in sorted(report["flows"], key=lambda x: x["id"]):
        raw = flows_by_id.get(f["id"], {})
        steps = [
            {
                "i": s.get("i"),
                "method": s.get("method"),
                "route": s.get("route"),
                "caller": s.get("caller"),
                "expect_status": s.get("expect_status"),
                "evidence": sorted(e for e in (_at(cv, a) for a in s.get("evidence") or []) if e),
            }
            for s in raw.get("steps", [])
            if s.get("kind") == "request"
        ]
        workflows.append({**f, "steps": steps})

    # ---- rules
    wf_of_rule: dict[str, list[str]] = {}
    for f in workflows:
        for r in f["rules"]:
            wf_of_rule.setdefault(r, []).append(f["id"])
    mc_of_rule: dict[str, list[str]] = {}
    for m in machines:
        for r in m["rules"]:
            mc_of_rule.setdefault(r, []).append(m["key"])
    rules: list[dict[str, Any]] = []
    for e in report.get("rules", []):
        row = text_of.get(e["id"])
        ev = sorted({loc for el in e["elements"] for loc in el["evidence"]})
        rules.append(
            {
                "id": e["id"],
                "text": row.text if row is not None else "",
                "spec_status": e["spec_status"],
                "impl_status": e["status"],
                "reasons": list(e.get("reasons") or []),
                "missing": list(e.get("missing") or []),
                "impact_label": e.get("impact_label"),
                "basis": BASIS_OF[e["status"]],
                "elements": [
                    {k: el[k] for k in ("key", "kind", "present", "verified", "basis")}
                    for el in e["elements"]
                ],
                "evidence": ev,
                "workflows": sorted(wf_of_rule.get(e["id"], [])),
                "machines": sorted(mc_of_rule.get(e["id"], [])),
                "cells": [
                    lk for lk in (row.links if row is not None else ()) if lk.startswith("cell:")
                ],
                "last_check": e.get("last_check"),
            }
        )

    # ---- access
    route_rows = {r["key"]: r for r in report["routes"]}
    endpoint_rows = {x["key"]: x for x in report["endpoints"]}
    observed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for f in workflows:
        if f["effective"] != "verified":
            continue
        for s in f["steps"]:
            observed.setdefault((str(s["caller"] or "no token"), str(s["route"])), []).append(
                {"flow": f["id"], "step": s["i"], "status": s["expect_status"]}
            )
    groups: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    for g in review.access_groups:
        groups.append({"prefix": g.prefix, "rows": list(g.rows), "columns": list(g.columns)})
        for rname in g.rows:
            for col in g.columns:
                key = _cell_route_key(col)
                route = route_rows.get(key)
                ep = endpoint_rows.get(key)
                cells.append(
                    {
                        "row": rname,
                        "col": col,
                        "spec": g.cells.get((rname, col), "·"),
                        "route": key if route is not None else None,
                        "auth": route["auth"] if route is not None else None,
                        "refusals": list(route["refusals"]) if route is not None else None,
                        "route_status": ep["status"] if ep is not None else None,
                        "observed": sorted(
                            observed.get((rname, key), []),
                            key=lambda o: (o["flow"], int(o["step"] or 0)),
                        ),
                        "evidence": list(route["evidence"]) if route is not None else [],
                    }
                )

    unread = [
        {**u, "at": cv.loc(u["file"], u["line"]) if u.get("file") else None}
        for u in report["unread"]
    ]

    # ---- summary and notes
    notes: list[dict[str, str]] = []
    if inventory.workflows is None:
        notes.append({"code": "no_workflows_capture", "text": NOTES["no_workflows_capture"]})
    elif inventory.run_mode != "observed":
        notes.append({"code": "static_capture", "text": NOTES["static_capture"]})
    if stale:
        notes.append({"code": "stale", "text": NOTES["stale"]})
    if trace is None:
        notes.append({"code": "no_trace", "text": NOTES["no_trace"]})
    notes.sort(key=lambda n: n["code"])
    specified = [c for c in cells if c["spec"] != "·"]
    summary = {
        "rules": {
            "total": len(rules),
            "impl_status": _count([r["impl_status"] for r in rules]),
            "spec_status": _count([r["spec_status"] for r in rules]),
            "basis": _count([r["basis"] for r in rules]),
        },
        "workflows": {
            "total": len(workflows),
            "effective": _count([f["effective"] for f in workflows]),
            "basis": _count([f["basis"] for f in workflows]),
        },
        "machines": {"total": len(machines), "status": _count([m["status"] for m in machines])},
        "access": {
            "groups": len(groups),
            "columns": sum(len(g["columns"]) for g in groups),
            "cells": len(cells),
            "specified": len(specified),
            "unspecified": len(cells) - len(specified),
            "with_route": sum(1 for c in cells if c["route"] is not None),
            "without_route": sum(1 for c in cells if c["route"] is None),
            "with_observed": sum(1 for c in cells if c["observed"]),
        },
        "endpoints": {
            "total": len(report["endpoints"]),
            "status": _count([x["status"] for x in report["endpoints"]]),
        },
        "unread": len(unread),
        "notes": notes,
    }
    inputs = {
        **(sources or sources_of(spec=None, trace=None, requirements=None)),
        "inventory": _inventory_input(inventory),
        "last_check": last_check_input(last_check),
    }
    doc = {
        "schema": SCHEMA,
        "state": "ok",
        "inputs": inputs,
        "summary": summary,
        "rules": rules,
        "workflows": workflows,
        "machines": machines,
        "access": {"groups": groups, "cells": cells},
        "endpoints": list(report["endpoints"]),
        "excluded": list(report["excluded"]),
        "unread": unread,
    }
    return AppMap(doc, report)


def build_appmap(
    spec: Spec,
    trace: Trace | None,
    inventory: impl.Inventory,
    review: ReviewData | None = None,
    last_check: Mapping[str, Any] | None = None,
    sources: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """``lattice.appmap/1`` as a dict (see :func:`build`)."""
    return build(spec, trace, inventory, review, last_check, sources).doc


def section_of(doc: Mapping[str, Any], section: str) -> dict[str, Any]:
    """One section: ``{schema, state, inputs, <section>}`` (``excluded`` travels with
    ``endpoints``). A non-``ok`` map is returned whole (it has no sections)."""
    if section not in SECTIONS:
        raise ValueError(f"section must be one of {', '.join(SECTIONS)}")
    if doc["state"] != "ok":
        return dict(doc)
    out = {k: doc[k] for k in ("schema", "state", "inputs")}
    out[section] = doc[section]
    if section == "endpoints":
        out["excluded"] = doc["excluded"]
    return out


# ---- elements and their neighbourhood ------------------------------------------------------------


def _build_index(am: AppMap) -> dict[str, tuple[str, Any]]:
    doc = am.doc
    idx: dict[str, tuple[str, Any]] = {}
    for r in doc["rules"]:
        idx[f"rule:{r['id']}"] = ("rule", r)
    for f in doc["workflows"]:
        idx[f["id"]] = ("workflow", f)
    for m in doc["machines"]:
        idx[m["key"]] = ("machine", m)
    for c in doc["access"]["cells"]:
        idx[f"cell:{c['row']}|{c['col']}"] = ("cell", c)
    routes = {r["key"]: r for r in am.report.get("routes", [])}
    for ep in doc["endpoints"]:
        idx[f"impl:route:{ep['key']}"] = ("route", {"endpoint": ep, "route": routes.get(ep["key"])})
        if ep["key"].startswith("RPC /"):  # an rpc is also listed under its own key (Spike I)
            idx[f"impl:rpc:{ep['key'][5:]}"] = idx[f"impl:route:{ep['key']}"]
    return idx


def element_keys(am: AppMap) -> list[str]:
    if am._index is None:
        am._index = _build_index(am)
    return sorted(am._index)


def _uniq(items: Sequence[str]) -> list[str]:
    return sorted(set(items))


def touches(am: AppMap, key: str) -> dict[str, list[str]]:
    """The map's own join keys (``rule:Rn``, ``wf-...``, ``impl:machine:T.c``, ``cell:row|col``,
    ``impl:route:K``) of every element that references the element `key`, or that it references:
    a lookup in the map's tables. Static: it says what is connected, never what fails."""
    if am._index is None:
        am._index = _build_index(am)
    kind, row = am._index[key]
    doc = am.doc
    out: dict[str, list[str]] = {
        "rules": [],
        "workflows": [],
        "machines": [],
        "cells": [],
        "routes": [],
    }
    if kind == "rule":
        out["workflows"] = list(row["workflows"])
        out["machines"] = list(row["machines"])
        out["cells"] = [c for c in row["cells"] if c in am._index]
        out["routes"] = [
            e["key"] for e in row["elements"] if e["kind"] == "route" and e["key"] in am._index
        ]
    elif kind == "workflow":
        out["rules"] = [f"rule:{r}" for r in row["rules"]]
        out["routes"] = [f"impl:route:{r}" for r in row["routes"] if f"impl:route:{r}" in am._index]
        refs = {
            m["key"]
            for m in doc["machines"]
            if m["id"] in row["machine_refs"] or m["key"] in row["machine_refs"]
        }
        out["machines"] = sorted(refs)
        out["cells"] = [
            f"cell:{c['row']}|{c['col']}"
            for c in doc["access"]["cells"]
            if any(o["flow"] == row["id"] for o in c["observed"])
        ]
    elif kind == "machine":
        out["rules"] = [f"rule:{r}" for r in row["rules"]]
        out["workflows"] = [
            f["id"]
            for f in doc["workflows"]
            if row["id"] in f["machine_refs"] or row["key"] in f["machine_refs"]
        ]
    elif kind == "route":
        ep = row["endpoint"]
        rt = row["route"]
        k = ep["key"]
        out["rules"] = [f"rule:{r}" for r in (rt["rules"] if rt else [])]
        out["workflows"] = list(rt["flows"]) if rt else []
        out["cells"] = [
            f"cell:{c['row']}|{c['col']}"
            for c in doc["access"]["cells"]
            if _cell_route_key(c["col"]) == k
        ]
    else:  # a cell
        k = _cell_route_key(row["col"])
        ck = f"cell:{row['row']}|{row['col']}"
        out["rules"] = [f"rule:{r['id']}" for r in doc["rules"] if ck in r["cells"]]
        out["routes"] = [f"impl:route:{k}"] if f"impl:route:{k}" in am._index else []
        out["workflows"] = _uniq([o["flow"] for o in row["observed"]])
    return {name: _uniq(vals) for name, vals in out.items()}


def element(am: AppMap, key: str) -> dict[str, Any]:
    """One element with its full row and its neighbourhood. Raises :class:`ElementError`
    (``bad_key``: empty or over 300 characters; ``unknown_key`` with the 5 nearest keys)."""
    if not isinstance(key, str) or not key or len(key) > MAX_KEY:
        raise ElementError("bad_key", f"a key is 1 to {MAX_KEY} characters")
    if am.state != "ok":
        raise ElementError("unknown_key", f"the map is {am.state}: it has no elements")
    if am._index is None:
        am._index = _build_index(am)
    if key not in am._index:
        near = difflib.get_close_matches(key, sorted(am._index), n=NEAREST, cutoff=0.0)
        raise ElementError("unknown_key", f"no element {key!r}", near)
    kind, row = am._index[key]
    return {
        "schema": SCHEMA,
        "state": "ok",
        "key": key,
        "kind": kind,
        "row": row,
        "touches": touches(am, key),
        "basis": "static",
        "meaning": MEANING,
    }


# ---- the refusals of the HTTP routes (the real server and the mock share them) -----------------


def refuse_section(section: str | None) -> tuple[str, str] | None:
    """(detail, code) of a 422 for a bad ``section``, else None."""
    if section is not None and section not in SECTIONS:
        return f"section must be one of {', '.join(SECTIONS)}", "bad_section"
    return None


def refuse_key(key: str | None) -> tuple[str, str] | None:
    """(detail, code) of a 422 for a missing or over-long ``key``, else None."""
    if not key or len(key) > MAX_KEY:
        return f"key must be 1 to {MAX_KEY} characters", "bad_key"
    return None


# ---- the project-level entry (the CLI and Studio share it) ---------------------------------------


def appmap_of_project(
    root: Path,
    *,
    spec: Spec | None,
    trace: Trace | None,
    sources: Mapping[str, str | None],
    last_check: Mapping[str, Any] | None = None,
    spec_error: str | None = None,
) -> AppMap:
    """The map of a project directory: the state when the map cannot be produced
    (``not_configured``, ``inventory_missing``, ``bad_inventory``, ``no_spec``), else ``ok``.
    Reads ``root/studio.toml`` and the inventory; writes nothing."""
    base_inputs = {**sources, "last_check": last_check_input(last_check)}
    cfg = impl.implementation_config(root)
    if cfg is None:
        return AppMap(
            non_ok(
                "not_configured",
                "studio.toml has no [implementation] table: add inventory and app_root",
                base_inputs,
            )
        )
    if spec is None or trace is None:
        return AppMap(
            non_ok("no_spec", spec_error or "the project has no loadable spec", base_inputs)
        )
    if cfg["inventory"] is None:
        return AppMap(
            non_ok(
                "inventory_missing",
                "inventory_missing: [implementation] names no inventory",
                base_inputs,
            )
        )
    try:
        inv = impl.load_inventory(cfg["inventory"], cfg["app_root"], cfg["package"])
    except impl.ImplementationError as err:
        state = "inventory_missing" if err.code == "inventory_missing" else "bad_inventory"
        return AppMap(non_ok(state, f"{err.code}: {err.detail}", base_inputs))
    return build(spec, trace, inv, None, last_check, sources)


def appmap_from_dir(root: Path, last_check: Mapping[str, Any] | None = None) -> AppMap:
    """The map of a project directory read straight from its files (the mock server and tests;
    Studio reads through its own snapshot and gets the same bytes)."""
    from lattice.spec.draft import load_spec

    root = Path(root)

    def read(name: str) -> str | None:
        try:
            return (root / name).read_text(encoding="utf-8")
        except OSError:
            return None

    mod, tr_text, md = read(_SPEC_FILE), read(_TRACE_FILE), read(_MD_FILE)
    sources = sources_of(spec=mod, trace=tr_text, requirements=md)
    spec = trace = None
    err: str | None = None
    if mod is None or tr_text is None:
        err = "no_spec: the project has no spec.py or spec.trace.json"
    else:
        spec, errors = load_spec(mod)
        if spec is None:
            err = "no_spec: spec.py does not load: " + "; ".join(errors)
        else:
            try:
                trace = Trace.from_json(tr_text)
            except (ValueError, KeyError, TypeError) as exc:
                spec, err = None, f"no_spec: spec.trace.json is not a trace ({exc})"
    return appmap_of_project(
        root, spec=spec, trace=trace, sources=sources, last_check=last_check, spec_error=err
    )


# ---- text ----------------------------------------------------------------------------------------


def render(doc: Mapping[str, Any]) -> str:
    """The map as text: the summary, then one line per element of each section."""
    if doc["state"] != "ok":
        return f"app map: {doc['state']}: {doc.get('why', '')}\n"
    s = doc["summary"]
    inv = doc["inputs"]["inventory"]
    out = [
        f"app map ({SCHEMA}): inventory {inv['run_mode'] or 'static'}"
        + (", STALE" if inv["stale"] else ""),
        f"rules {s['rules']['total']}: {s['rules']['impl_status']}",
        f"workflows {s['workflows']['total']}: {s['workflows']['effective']}",
        f"machines {s['machines']['total']}: {s['machines']['status']}",
        f"access cells {s['access']['cells']} ({s['access']['specified']} specified, "
        f"{s['access']['with_observed']} with an observed answer)",
        f"endpoints {s['endpoints']['total']}: {s['endpoints']['status']}",
        f"not read {s['unread']}",
    ]
    out += [f"note {n['code']}: {n['text']}" for n in s["notes"]]
    out.append("")
    out.append("rules (id, implementation status, basis, evidence count, text)")
    for r in doc["rules"]:
        out.append(
            f"  {r['id']:<6} {r['impl_status']:<26} {r['basis']:<9} "
            f"{len(r['evidence']):>3}  {r['text'][:80]}"
        )
    out.append("workflows (id, status, basis, name)")
    for f in doc["workflows"]:
        out.append(f"  {f['id']:<15} {f['effective']:<11} {f['basis']:<9} {f['name'][:70]}")
    out.append("machines")
    for m in doc["machines"]:
        out.append(f"  {m['key']} {m['status']} states={m['states']} rules={m['rules']}")
        for e in m["edges_detail"]:
            out.append(
                f"    {e['from']} -> {e['to']}  in_spec={e['in_spec']} in_code={e['in_code']}"
            )
    out.append("access (row, endpoint: spec | code auth | observed)")
    for c in doc["access"]["cells"]:
        if c["spec"] == "·" and not c["observed"]:
            continue
        obs = ",".join(str(o["status"]) for o in c["observed"]) or "-"
        out.append(f"  {c['row']:<9} {c['col']}: {c['spec']} | {c['auth']} | {obs}")
    out.append("endpoints not implemented or not required")
    for x in doc["endpoints"]:
        if x["status"] != "implemented":
            out.append(f"  {x['key']} {x['status']}")
    out.append("not read")
    for u in doc["unread"]:
        out.append(f"  {u['at']}: {u['what']}: {u['reason']}")
    return "\n".join(out) + "\n"


# ---- the CLI -------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m lattice.spec appmap",
        description="The app map: what the code implements of what the spec requires, with the "
        "evidence (a join of existing reports; read-only).",
    )
    p.add_argument("--spec", required=True, help="module:SPEC (a Spec, or a function)")
    p.add_argument("--trace", type=Path, help="the spec's trace JSON (without one: no rules)")
    p.add_argument("--inventory", type=Path, required=True, help="the inventory directory")
    p.add_argument("--app-root", type=Path, default=Path("."), help="the app's root (default .)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--section", choices=SECTIONS)
    return p


def _read_bytes(path: Path | None) -> bytes | None:
    try:
        return path.read_bytes() if path is not None else None
    except OSError:
        return None


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0: the map was produced; 2: it could not be. Writes nothing."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "appmap":
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
        inv = impl.load_inventory(args.inventory, args.app_root)
        module = sys.modules.get(args.spec.partition(":")[0])
        spec_file = Path(module.__file__) if module is not None and module.__file__ else None
        md = None
        if args.trace is not None:
            src = Path(trace.source).name if trace is not None and trace.source else _MD_FILE
            md = _read_bytes(args.trace.parent / src) or _read_bytes(args.trace.parent / _MD_FILE)
        sources = sources_of(
            spec=_read_bytes(spec_file), trace=_read_bytes(args.trace), requirements=md
        )
        am = build(spec, trace, inv, None, None, sources)
    except impl.ImplementationError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except Exception as err:  # noqa: BLE001 -- any failure to produce the map is exit 2
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    doc = section_of(am.doc, args.section) if args.section else am.doc
    sys.stdout.write(map_json(doc) if args.json or args.section else render(am.doc))
    return 0


__all__ = [
    "AppMap",
    "ElementError",
    "SCHEMA",
    "SECTIONS",
    "STATES",
    "appmap_from_dir",
    "appmap_of_project",
    "build",
    "build_appmap",
    "element",
    "element_keys",
    "map_json",
    "non_ok",
    "refuse_key",
    "refuse_section",
    "section_of",
    "sources_of",
    "touches",
]

if __name__ == "__main__":
    sys.exit(main())
