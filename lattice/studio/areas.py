"""Areas of a spec and the findings-to-scope mapping of "build from spec" (API.md v1.8).

Pure functions over the trace (``spec.trace.json``) and gate findings; no I/O, no git.

**Areas** (:func:`derive_areas`): the trace's rules grouped by their top-level requirements
section, in document order. A rule's ``section`` is its heading path joined by ``" > "``
(``lattice.spec.rules.extract_rules``), e.g. ``"Streak: requirements > Habits"``. When every
sectioned rule shares one first heading (the document title) and some go deeper, the area
is the second heading (``"Habits"``) and the rules directly under the title form a preamble
area named after the title; otherwise the area is the first heading. Rules without a
section form one ``"Requirements"`` area. The last area is always the virtual ``"all"``
(every rule). ``[rebuild] areas = "none"`` keeps only ``"all"``.

An area's **targets** are the trace links of its rules (``scenario:...``,
``invariant:...``, ...), in order, without repeats; ``scenarios`` counts the
``scenario:`` ones. A target linked from rules of two areas belongs to both.

**Findings and scope.** A gate finding has a ``target`` (``scenario:<name>`` etc., or a
rule id, or ``""``). Its **identity** (:func:`finding_identity`) is the target when it has
one, else ``key:<key>`` (a ``findings_json`` finding's ``key``), else ``title:<title>``: two
findings with one identity are the same check failing. A finding is **in scope** when its
target is linked from (or is) a rule of the scope; an unmapped finding (no target, or a
target no rule links to) is out of scope except for the scope ``"all"``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

__all__ = [
    "ALL",
    "area_view",
    "derive_areas",
    "finding_identity",
    "findings_from_json",
    "normalize_target",
    "resolve_scope",
    "scope_slug",
    "slug",
    "target_rules",
]

ALL = "all"
SEP = " > "
SLUG_MAX = 48
_TARGET = re.compile(
    r"^\s*(scenario|invariant|machine|service|profile|cell|contract|file)\s*:\s*(\S+)"
)
_RULE_ID = re.compile(r"^R\d+$")


class ScopeError(Exception):
    """A scope that cannot be resolved: ``status`` 422 (malformed) or 404 (unknown)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def slug(text: str, n: int = SLUG_MAX) -> str:
    """A url-safe id: lowercase words joined by ``-``, at most `n` characters."""
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    if len(s) > n:
        s = s[:n].rsplit("-", 1)[0] if "-" in s[:n] else s[:n]
    return s or "area"


def _rules(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in trace.get("rules") or []:
        if isinstance(r, Mapping) and r.get("id"):
            out.append(dict(r))
    return out


def _links(trace: Mapping[str, Any]) -> dict[str, list[str]]:
    links = trace.get("links") or {}
    if not isinstance(links, Mapping):
        return {}
    return {str(k): [str(t) for t in v or []] for k, v in links.items()}


def _uniq(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def derive_areas(trace: Mapping[str, Any], mode: str = "sections") -> list[dict[str, Any]]:
    """The areas of `trace` (see the module docstring): ``[{id, title, rules, targets}]``,
    the virtual ``all`` last."""
    rules = _rules(trace)
    links = _links(trace)
    groups: dict[str, list[str]] = {}
    if mode != "none":
        parts = [
            [p.strip() for p in str(r.get("section") or "").split(SEP) if p.strip()] for r in rules
        ]
        firsts = {p[0] for p in parts if p}
        root = next(iter(firsts)) if len(firsts) == 1 and any(len(p) > 1 for p in parts) else None
        for r, p in zip(rules, parts, strict=True):
            if not p:
                title = "Requirements"
            elif root is not None:
                title = p[1] if len(p) > 1 else root
            else:
                title = p[0]
            groups.setdefault(title, []).append(str(r["id"]))
    out: list[dict[str, Any]] = []
    used = {ALL}
    for title, rids in groups.items():
        base = slug(title)
        aid, n = base, 2
        while aid in used:
            aid, n = f"{base}-{n}", n + 1
        used.add(aid)
        targets = _uniq(t for rid in rids for t in links.get(rid, []))
        out.append({"id": aid, "title": title, "rules": rids, "targets": targets})
    every = [str(r["id"]) for r in rules]
    out.append(
        {
            "id": ALL,
            "title": "All",
            "rules": every,
            "targets": _uniq(t for rid in every for t in links.get(rid, [])),
        }
    )
    return out


def target_rules(trace: Mapping[str, Any]) -> dict[str, list[str]]:
    """target -> the rules linking to it (the links inverted)."""
    out: dict[str, list[str]] = {}
    for rid, targets in _links(trace).items():
        for t in targets:
            out.setdefault(t, [])
            if rid not in out[t]:
                out[t].append(rid)
    return out


def normalize_target(text: Any) -> str:
    """``"scenario: x"`` -> ``"scenario:x"``; a rule id stays; anything else is ``""``."""
    s = str(text or "").strip()
    m = _TARGET.match(s)
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    head = s.split(":", 1)[0].split()[0] if s else ""
    return head if _RULE_ID.match(head) else ""


def finding_identity(f: Mapping[str, Any]) -> str:
    if f.get("target"):
        return str(f["target"])
    if f.get("key"):
        return f"key:{f['key']}"
    return f"title:{f.get('title') or ''}"


def _short(text: str, n: int = 300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def findings_from_json(data: Mapping[str, Any]) -> tuple[dict[str, int], list[dict[str, str]]]:
    """(counts, findings) from a ``lattice.spec check --json`` report (``[rebuild]
    findings_json``). Relied on: ``counts`` ({layer: n}) and ``findings``, a list of
    objects with ``layer``, ``verdict``, ``rule`` (``"scenario: <name>"``,
    ``"invariant: <name>"`` or a rule id), ``case``, ``details.problems`` (strings) and
    ``key``; ``verdict``, ``layers``, ``cases`` and ``notes`` are not needed."""
    counts = {
        str(k): int(v)
        for k, v in (data.get("counts") or {}).items()
        if isinstance(v, int) and not isinstance(v, bool)
    }
    findings: list[dict[str, str]] = []
    for f in data.get("findings") or []:
        if not isinstance(f, Mapping):
            continue
        problems = (
            (f.get("details") or {}).get("problems")
            if isinstance(f.get("details"), Mapping)
            else None
        )
        what = "; ".join(str(p) for p in problems or [] if p) or str(f.get("summary") or "")
        head = " ".join(
            str(x) for x in (f.get("layer"), f.get("verdict"), f.get("case") or f.get("rule")) if x
        )
        findings.append(
            {
                "title": _short(f"{head}: {what}" if what else head),
                "target": normalize_target(f.get("rule")),
                "key": str(f.get("key") or ""),
            }
        )
    return counts, findings


def resolve_scope(
    scope: Any, areas: Sequence[Mapping[str, Any]], trace: Mapping[str, Any]
) -> dict[str, Any]:
    """The POST body's ``scope`` as ``{kind, id, title, rules, targets}`` (kind ``area``,
    ``rules`` or ``all``). Raises :class:`ScopeError` (422 malformed, 404 unknown)."""
    bad = 'scope must be "all", {"area": "<id>"} or {"rules": ["<id>", ...]}'
    by_id = {str(a["id"]): a for a in areas}
    if scope == ALL or (
        isinstance(scope, Mapping) and scope.get("area") == ALL and len(scope) == 1
    ):
        a = by_id[ALL]
        return {"kind": ALL, "id": ALL, "title": "All", "rules": list(a["rules"]),
                "targets": list(a["targets"])}  # fmt: skip
    if not isinstance(scope, Mapping) or len(scope) != 1:
        raise ScopeError(422, bad)
    if "area" in scope:
        aid = scope["area"]
        if not isinstance(aid, str) or not aid:
            raise ScopeError(422, bad)
        a = by_id.get(aid)
        if a is None:
            raise ScopeError(404, f"no area {aid}")
        return {"kind": "area", "id": aid, "title": str(a["title"]), "rules": list(a["rules"]),
                "targets": list(a["targets"])}  # fmt: skip
    if "rules" in scope:
        ids = scope["rules"]
        if (
            not isinstance(ids, list)
            or not ids
            or not all(isinstance(i, str) and i for i in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ScopeError(422, bad)
        known = {str(r["id"]) for r in _rules(trace)}
        missing = [i for i in ids if i not in known]
        if missing:
            raise ScopeError(404, f"no rule {', '.join(missing)}")
        links = _links(trace)
        return {
            "kind": "rules",
            "id": scope_slug({"kind": "rules", "rules": ids}),
            "title": "rules " + ", ".join(ids),
            "rules": list(ids),
            "targets": _uniq(t for rid in ids for t in links.get(rid, [])),
        }
    raise ScopeError(422, bad)


def scope_slug(scope: Mapping[str, Any]) -> str:
    """The branch / metrics-phase name of a resolved scope."""
    if scope.get("kind") == "rules":
        rules = list(scope.get("rules") or [])
        s = "rules-" + "-".join(rules)
        return slug(s, 40) if len(s) <= 40 else slug(f"rules-{rules[0]}-and-{len(rules) - 1}")
    return str(scope.get("id") or ALL)


def area_view(
    area: Mapping[str, Any], failing: set[str] | None, building: bool = False
) -> dict[str, Any]:
    """One ``GET /api/areas`` item: `failing` is the set of failing identities of the
    latest gate results (None when no check ran)."""
    targets = list(area.get("targets") or [])
    n_fail = None if failing is None else sum(1 for t in targets if t in failing)
    if building:
        status = "building"
    elif n_fail is None:
        status = "not_built"
    elif n_fail == 0:
        status = "built"
    elif n_fail >= len(targets):
        status = "not_built"
    else:
        status = "partial"
    return {
        "id": area["id"],
        "title": area["title"],
        "rules": list(area.get("rules") or []),
        "scenarios": sum(1 for t in targets if t.startswith("scenario:")),
        "checks": len(targets),
        "targets": targets,
        "failing": n_fail,
        "status": status,
    }
