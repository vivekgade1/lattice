"""The import report and the ``lattice_import/`` writer (spec 2026-09-27 importer §3.5,
plan nodes I4 and I5).

::

    lattice_import/
      profile.py                  # the generated ApiProfile (+ R3 carve-out natives)
      natives.py                  # the originals behind helpers, carve-outs and islands
      routes/<router>.lattice.py  # verified routes only, one program per router file
      report.md / report.json

:func:`write_import` writes the directory atomically (a sibling temporary directory
swapped in), so an interrupted or budget-exhausted run never leaves a half-written
import, and every file agrees with ``report.json``: the programs hold exactly the
routes the report calls verified, and every route of the app is in the report once.

``report.json`` (schema :data:`SCHEMA`): per route -- how it was lifted (lifter or LLM,
the rung), its tier (T0-T3, or island) and R3 carve kind (procedure / native / none)
with its procedures (count and lines), the verification tally and declared deviations,
the island reason and gap, LLM rounds and cost; the coverage (routes, and the
original's lines and AST nodes, by rung; the tier mix and the carve kinds); behaviour
changes needing human acceptance; unclear profile decisions; notes-vs-
behaviour disagreements; the budget; tokens, cost and wall time.

Schema 2 adds ``rules`` (Phase 2 plan 2026-10-05, §1.3 and §5): layer R of the candidate
spec on the original -- the summary, every rule item (verdict, probes, reason, the
observed fact when refuted), the table and route verdicts, the probes and notes
(``lattice.spec.probes.RuleResult.to_dict()``) -- and ``coverage.rules``, its summary.
Both are None when the rule layer did not run. A ``--rules-only`` import also writes
``candidate_spec.py`` (the candidate spec module, ``lattice.importer.emit_spec``) and
``proposals.json`` (the mined proposals, each pending review: ``lattice.importer.review``).
"""

from __future__ import annotations

import ast
import json
import shutil
import uuid
from collections.abc import Mapping
from pathlib import Path
from pprint import pformat
from typing import Any

from lattice.importer.extract import route_function
from lattice.importer.llm_lift import CloseResult, RouteOutcome

__all__ = ["SCHEMA", "load_report", "render_markdown", "report_data", "write_import"]

SCHEMA = "lattice-import-report/2"
RUNGS = ("R0", "R1", "R2", "R3", "R4", "needs-llm")
TIERS = ("T0", "T1", "T2", "T3", "island", "needs-llm")
KINDS = ("procedure", "native")  # R3 carve kinds


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def _size(close: CloseResult, o: RouteOutcome) -> tuple[int, int]:
    """(lines, AST nodes) of the original route function (decorators included)."""
    ctx = close.result.context
    if ctx is None or o.route.module not in ctx.index.modules:
        return 0, 0
    fn = route_function(ctx.index, o.route)
    if fn is None:
        return 0, 0
    first = min([fn.lineno, *(d.lineno for d in fn.decorator_list)])
    return (fn.end_lineno or fn.lineno) - first + 1, sum(1 for _ in ast.walk(fn))


def _deviations(o: RouteOutcome) -> list[dict[str, Any]]:
    v = o.verified
    if v is None or v.verify is None:
        return []
    out = []
    for name, outcome in v.verify.outcomes.items():
        if outcome.kind != "deviation":
            continue
        d = v.deviations.get(name)
        out.append({
            "case": name,
            "id": outcome.deviation,
            "kind": d.kind if d else None,
            "status": d.status if d else None,
            "reason": d.reason if d else "",
        })  # fmt: skip
    return out


def _route(close: CloseResult, o: RouteOutcome) -> dict[str, Any]:
    r = o.result
    lines, nodes = _size(close, o)
    tally = o.verified.tally() if o.verified is not None else None
    return {
        "key": o.key,
        "name": r.route.name,
        "module": r.route.module,
        "router": r.router,
        "method": r.route.method,
        "path": r.route.path,
        "auth": r.route.auth,
        "status": o.status,
        "rung": o.rung,
        "by": o.by,
        "lifters": list(r.lift.lifters),
        "lifter_reason": r.reason,
        "reason": o.reason,
        "gap": o.gap,
        "tally": dict(
            zip(("equal", "equal_under_rule", "deviation", "mismatch"), tally, strict=True)
        )
        if tally
        else None,  # fmt: skip
        "deviations": _deviations(o),
        "rounds": o.rounds,
        "cost_usd": round(o.cost_usd, 6),
        "attempts": [a.to_dict() for a in o.attempts],
        "natives": [n.to_dict() for n in o.natives],
        "tier": o.tier or "needs-llm",
        "carve_kind": o.carve,
        "procedure_count": len(o.procedures),
        "procedure_lines": sum(p.lines for p in o.procedures),
        "procedures": [p.to_dict() for p in o.procedures],
        "notes": list(o.notes),
        "original": {"lines": lines, "ast_nodes": nodes},
    }


def report_data(
    close: CloseResult,
    *,
    repo_root: Path | str | None = None,
    package: str | None = None,
    app_factory: str | None = None,
    rules: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The report's data; ``rules``: layer R's result (``RuleResult.to_dict()``), None
    when the rule layer did not run."""
    routes = [_route(close, o) for o in close.outcomes]
    n = len(routes)
    count = {rung: sum(1 for r in routes if r["rung"] == rung) for rung in RUNGS}
    lattice = count["R0"] + count["R1"] + count["R2"]
    size = {"lines": {}, "ast_nodes": {}}  # type: dict[str, dict[str, int]]
    for r in routes:
        group = {"R0": "lattice", "R1": "lattice", "R2": "lattice", "R3": "carve_out",
                 "R4": "island", "needs-llm": "needs_llm"}[r["rung"]]  # fmt: skip
        for k in ("lines", "ast_nodes"):
            size[k][group] = size[k].get(group, 0) + r["original"][k]
    changes: dict[tuple[str, str], dict[str, Any]] = {}
    for r in routes:
        for d in r["deviations"]:
            entry = changes.setdefault(
                (r["key"], d["id"]),
                {"route": r["key"], "id": d["id"], "kind": d["kind"], "reason": d["reason"],
                 "cases": []},
            )  # fmt: skip
            entry["cases"].append(d["case"])
    verified = count["R0"] + count["R1"] + count["R2"] + count["R3"]
    cost = close.cost_usd
    llm_verified = count["R1"] + count["R2"] + count["R3"]
    return {
        "schema": SCHEMA,
        "repo": str(repo_root) if repo_root is not None else None,
        "package": package or close.result.model.app_package,
        "app_factory": app_factory,
        "llm": close.llm,
        "method": close.method,
        "model": close.model,
        "coverage": {
            "routes": n,
            **count,
            "verified": verified,
            "lattice_pct": _pct(lattice, n),
            "carve_out_pct": _pct(count["R3"], n),
            "island_pct": _pct(count["R4"], n),
            "needs_llm_pct": _pct(count["needs-llm"], n),
            "verified_pct": _pct(verified, n),
            "complete": verified == n,
            "lines": size["lines"],
            "ast_nodes": size["ast_nodes"],
            "tiers": {t: sum(1 for r in routes if r["tier"] == t) for t in TIERS},
            "carve_kinds": {k: sum(1 for r in routes if r["carve_kind"] == k) for k in KINDS},
            "rules": dict(rules["summary"]) if rules is not None else None,
        },
        "budget": close.budget.to_dict() if close.budget is not None else None,
        "cost": {
            "usd": round(cost, 6),
            "notes_usd": round(close.notes_cost_usd, 6),
            "per_llm_verified_route_usd": round(cost / llm_verified, 6) if llm_verified else None,
            "input_tokens": close.input_tokens,
            "output_tokens": close.output_tokens,
            "llm_rounds": sum(r["rounds"] for r in routes),
            "wall_s": round(close.wall_s, 3),
        },
        "routes": routes,
        "behaviour_changes": list(changes.values()),
        "islands": [
            {"route": r["key"], "reason": r["reason"], "gap": r["gap"]}
            for r in routes
            if r["status"] == "island"
        ],
        "carve_out_natives": [n.to_dict() for n in close.carve_natives],
        "unclear": [
            {
                "topic": u.topic,
                "subject": u.subject,
                "reason": u.reason,
                "candidates": list(u.candidates),
            }
            for u in close.result.unclear
        ],  # fmt: skip
        "notes_disagreements": [d.to_dict() for d in close.disagreements],
        "rules": dict(rules) if rules is not None else None,
    }


def _cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def _tier_line(cov: Mapping[str, Any]) -> str:
    tiers = cov.get("tiers") or {}
    kinds = cov.get("carve_kinds") or {}
    mix = ", ".join(f"{t} {tiers.get(t, 0)}" for t in ("T0", "T1", "T2", "T3"))
    return (
        f"- tier mix: {mix} (islands {tiers.get('island', 0)}); R3 carve-outs: "
        f"{kinds.get('procedure', 0)} procedure, {kinds.get('native', 0)} native"
    )


def _carve_cell(r: Mapping[str, Any]) -> str:
    kind = r.get("carve_kind", "none")
    if kind == "procedure":
        return f"procedure ({r['procedure_count']}, {r['procedure_lines']} lines)"
    if kind == "native":
        return f"native ({len(r['natives'])})"
    return ""


def render_markdown(data: Mapping[str, Any]) -> str:
    cov = data["coverage"]
    lines = [
        f"# Lattice import of `{data['package']}`",
        "",
        f"- LLM loop: {'on' if data['llm'] else 'off (--no-llm)'}"
        + (f", method {data['method']}, model {data['model']}" if data["llm"] else ""),
        f"- routes: {cov['routes']}; verified {cov['verified']} ({cov['verified_pct']}%): "
        f"Lattice R0-R2 {cov['R0'] + cov['R1'] + cov['R2']} ({cov['lattice_pct']}%), "
        f"carve-out R3 {cov['R3']} ({cov['carve_out_pct']}%)",
        f"- islands R4: {cov['R4']} ({cov['island_pct']}%); needing the LLM: "
        f"{cov['needs-llm']} ({cov['needs_llm_pct']}%)",
        f"- by rung: R0 {cov['R0']}, R1 {cov['R1']}, R2 {cov['R2']}, R3 {cov['R3']}, "
        f"R4 {cov['R4']}",
        f"- original lines by outcome: {cov['lines']}; AST nodes: {cov['ast_nodes']}",
        _tier_line(cov),
        f"- cost: ${data['cost']['usd']:.4f} (notes ${data['cost']['notes_usd']:.4f}), "
        f"{data['cost']['llm_rounds']} LLM rounds, {data['cost']['input_tokens']} input / "
        f"{data['cost']['output_tokens']} output tokens, {data['cost']['wall_s']} s",
    ]
    if data.get("budget"):
        b = data["budget"]
        state = f"exhausted -- {b['exhausted_by']}" if b["exhausted"] else "not exhausted"
        lines.append(f"- budget: {state} (spent ${b['cost_usd']:.4f}, {b['rounds']} rounds)")
    lines += [
        "",
        "## Routes",
        "",
        "| route | request | status | rung | by | tier | carve | tally eq/rule/dev/mis | rounds "
        "| cost | reason |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in data["routes"]:
        t = r["tally"]
        tally = "/".join(str(v) for v in t.values()) if t else ""
        lines.append(
            f"| {_cell(r['key'])} | {_cell(r['method'] + ' ' + r['path'])} | {r['status']} | "
            f"{r['rung']} | {_cell(r['by'])} | {r.get('tier', '')} | {_carve_cell(r)} | "
            f"{tally} | {r['rounds']} | "
            f"${r['cost_usd']:.4f} | {_cell((r['reason'] or r['lifter_reason'])[:160])} |"
        )
    if data["behaviour_changes"]:
        lines += ["", "## Behaviour changes (need human acceptance)", ""]
        for c in data["behaviour_changes"]:
            lines.append(
                f"- `{c['route']}` {c['id']} ({len(c['cases'])} cases): {_cell(c['reason'])}"
            )
    if data["islands"]:
        lines += ["", "## Islands", ""]
        for i in data["islands"]:
            lines.append(f"- `{i['route']}` [{i['gap']}]: {_cell(i['reason'][:400])}")
    if data["carve_out_natives"]:
        lines += ["", "## Carve-out natives", ""]
        for n in data["carve_out_natives"]:
            kind = "effect" if n["effect"] else "pure"
            lines.append(f"- `{n['name']}` wraps `{n['target']}` ({kind})")
    if data["unclear"]:
        lines += ["", "## Unclear profile decisions", ""]
        lines += [f"- {u['topic']} {u['subject']}: {_cell(u['reason'])}" for u in data["unclear"]]
    if data["notes_disagreements"]:
        lines += ["", "## Intent notes vs behaviour", ""]
        for d in data["notes_disagreements"]:
            lines.append(
                f"- `{d['route']}` {d['field']}: the notes say {d['notes']}; "
                f"the code/behaviour says {d['behaviour']}"
            )
    lines += _rules_markdown(data.get("rules"))
    return "\n".join(lines) + "\n"


def _rules_markdown(rules: Mapping[str, Any] | None) -> list[str]:
    if not rules:
        return []
    s = rules["summary"]
    items = ", ".join(f"{v} {n}" for v, n in s["items"].items())
    lines = [
        "",
        "## Rule layer (layer R on the original)",
        "",
        f"- tables verified: {s['tables']['verified']} of {s['tables']['total']}; routes "
        f"verified: {s['routes']['verified']} of {s['routes']['total']}; items: {items}",
        "",
        "| item | verdict | reason |",
        "|---|---|---|",
    ]
    for item_id, v in rules["items"].items():
        if v["verdict"] != "verified":
            lines.append(f"| {_cell(item_id)} | {v['verdict']} | {_cell(v['reason'][:200])} |")
    return lines


def natives_source(close: CloseResult) -> str:
    """``natives.py``: the original functions behind the profile's natives (helpers), the
    R3 carve-out natives and the islands (with a wrapper each), and the routes still
    needing the LLM."""
    model = close.result.model
    helpers = {n.name: n.origin for n in model.natives}
    carve = {n.name: n.target for n in close.carve_natives}
    out = [
        f'"""Natives of the Lattice import of {model.app_package}.',
        "",
        "Generated by lattice.importer: the originals are referenced, never copied. HELPERS",
        "back the profile's natives (typed wrappers in profile.py), CARVE_OUTS the R3",
        "carve-out natives; ISLANDS are whole routes kept as the original code, with the",
        'reason; NEEDS_LLM are routes not lifted in a --no-llm run."""',
        "",
        "from __future__ import annotations",
        "",
        "import importlib",
        "from typing import Any",
        "",
        "",
        "def original(qual: str) -> Any:",
        '    """The original object ``module:name``."""',
        '    module, _, name = qual.partition(":")',
        "    obj: Any = importlib.import_module(module)",
        '    for part in name.split("."):',
        "        obj = getattr(obj, part)",
        "    return obj",
        "",
        "",
        f"HELPERS: dict[str, str] = {pformat(helpers)}",
        "",
        f"CARVE_OUTS: dict[str, str] = {pformat(carve)}",
        "",
    ]
    islands: dict[str, dict[str, Any]] = {}
    pending: dict[str, dict[str, Any]] = {}
    for o in close.outcomes:
        entry = {
            "function": f"{o.route.module}:{o.route.name}",
            "method": o.route.method,
            "path": o.route.path,
            "reason": o.reason or o.result.reason,
        }
        if o.status == "island":
            islands[o.key] = {**entry, "gap": o.gap}
        elif o.status == "needs-llm":
            pending[o.key] = entry
    out += [f"ISLANDS: dict[str, dict[str, Any]] = {pformat(islands)}", ""]
    out += [f"NEEDS_LLM: dict[str, dict[str, Any]] = {pformat(pending)}", ""]
    for key, entry in islands.items():
        name = "island_" + "".join(c if c.isalnum() else "_" for c in key)
        doc = f"Island {entry['method']} {entry['path']}: the original route, unchanged."
        out += [
            "",
            f"def {name}(*args: Any, **kwargs: Any) -> Any:",
            f"    {doc!r}",
            f"    return original({entry['function']!r})(*args, **kwargs)",
        ]
    return "\n".join(out) + "\n"


def write_import(
    close: CloseResult,
    out: Path | str,
    *,
    repo_root: Path | str | None = None,
    package: str | None = None,
    app_factory: str | None = None,
    rules: Mapping[str, Any] | None = None,
    extra_files: Mapping[str, str] | None = None,
) -> Path:
    """Write ``lattice_import/`` at ``out`` (atomically; see the module docstring).
    ``rules``: layer R's result for the report; ``extra_files``: more files (name ->
    text) to write in the directory (a rules import's ``candidate_spec.py`` and
    ``proposals.json``)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / f".{out.name}.tmp-{uuid.uuid4().hex[:8]}"
    tmp.mkdir()
    try:
        data = report_data(close, repo_root=repo_root, package=package, app_factory=app_factory,
                           rules=rules)  # fmt: skip
        _check_consistent(close, data)
        (tmp / "profile.py").write_text(close.profile_source)
        (tmp / "natives.py").write_text(natives_source(close))
        routes = tmp / "routes"
        routes.mkdir()
        for router, source in close.programs.items():
            (routes / f"{router}.lattice.py").write_text(source)
        (tmp / "report.json").write_text(json.dumps(data, indent=2, default=str) + "\n")
        (tmp / "report.md").write_text(render_markdown(data))
        for name, text in (extra_files or {}).items():
            (tmp / name).write_text(text)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    old = None
    if out.exists():
        old = out.parent / f".{out.name}.old-{uuid.uuid4().hex[:8]}"
        out.rename(old)
    tmp.rename(out)
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)
    return out


def _check_consistent(close: CloseResult, data: Mapping[str, Any]) -> None:
    """The programs hold exactly the verified routes; every route is reported once."""
    emitted: set[str] = set()
    for source in close.programs.values():
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef) and any(
                isinstance(d, ast.Call) and getattr(d.func, "id", None) == "endpoint"
                for d in node.decorator_list
            ):
                emitted.add(node.name)
    verified = {r["name"] for r in data["routes"] if r["status"] == "verified"}
    if emitted != verified:
        raise AssertionError(f"programs {sorted(emitted)} != verified routes {sorted(verified)}")
    keys = [r["key"] for r in data["routes"]]
    if len(keys) != len(set(keys)) or len(keys) != len(close.result.results):
        raise AssertionError("the report does not hold every route exactly once")


def load_report(out: Path | str) -> dict[str, Any]:
    return json.loads((Path(out) / "report.json").read_text())
