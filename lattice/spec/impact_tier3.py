"""Tier 3 of the change-impact report (docs/results/2026-10-16-change-impact-prereg.md, D5): the
code a spec change touches, read statically from the inventory's extract. Sound but incomplete: it
lists locations, never claims a failure, and names what it could not read (``unread``).

No app, no ``go``, no git, no grep over names. Seeds come from the rules the diff touched (through
Spike C's :class:`~lattice.spec.implementation.ImplContext`), from the changed spec elements, and
from the requests T1 saw fail; each seed route expands into locations: its registration, handler,
the raises and parameters a changed expectation names (``direct``), the declarations in its
``calls`` (``closure``), the registrations of other routes on a changed table (``table_peer``),
and, for an endpoint the code lacks, the registering functions of its siblings (``insertion``).
A function is found by a lexical declaration match in the module's source (R6): one match or
``unread``, never a guess. Every location carries a ``via`` chain that ends in an extract fact.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.spec.implementation import (
    ImplContext,
    Inventory,
    route_key,
    routes_in_text,
    template_path,
)
from lattice.spec.incremental import RuleDiff
from lattice.spec.model import Await, Spec, Step
from lattice.spec.rules import Trace

KINDS = ("direct", "insertion", "closure", "table_peer")
_FUNC = re.compile(r"^func\b")
_TYPE = re.compile(r"^type\s+(\w+)")
_LITERAL = re.compile(r"^(.*)\.func(\d+)$")
_END = re.compile(r"^\}")
_PLUMBING = ("/health", "/healthz", "/livez", "/readyz", "/ready", "/ping", "/metrics", "/version")


@dataclass
class Decl:
    name: str
    recv: str | None
    start: int
    end: int
    kind: str  # func | type


class GoTree:
    """The Go source under the app root: lexical top-level declarations, cached per file."""

    def __init__(self, inv: Inventory) -> None:
        self.inv = inv
        self._lines: dict[str, list[str] | None] = {}
        self._decls: dict[str, list[Decl]] = {}

    def lines(self, rel: str) -> list[str] | None:
        if rel not in self._lines:
            try:
                text = (Path(self.inv.app_root) / rel).read_text(encoding="utf-8", errors="replace")
                self._lines[rel] = text.replace("\r\n", "\n").split("\n")
            except OSError:
                self._lines[rel] = None
        return self._lines[rel]

    def decls(self, rel: str) -> list[Decl]:
        if rel in self._decls:
            return self._decls[rel]
        out: list[Decl] = []
        lines = self.lines(rel) or []
        i = 0
        while i < len(lines):
            line = lines[i]
            kind, name, recv = None, None, None
            if _FUNC.match(line):
                m = re.match(
                    r"^func\s*(?:\(\s*\w*\s*\*?\s*(\w+)(?:\[[^\]]*\])?\s*\)\s*)?(\w+)\s*[\[(]", line
                )
                if m:
                    kind, recv, name = "func", m.group(1), m.group(2)
            else:
                t = _TYPE.match(line)
                if t:
                    kind, name = "type", t.group(1)
            if kind and name:
                end = i
                opens = line.count("{") - line.count("}")
                if "{" in line and opens <= 0:
                    end = i
                elif line.rstrip().endswith(("(", "{")) or opens > 0:
                    j = i + 1
                    while j < len(lines) and not _END.match(lines[j]):
                        j += 1
                    end = min(j, len(lines) - 1)
                out.append(Decl(name, recv, i + 1, end + 1, kind))
                i = end + 1
                continue
            i += 1
        self._decls[rel] = out
        return out

    def enclosing(self, rel: str, line: int) -> Decl | None:
        for d in self.decls(rel):
            if d.start <= line <= d.end:
                return d
        return None

    def go_files(self, directory: str) -> list[str]:
        root = Path(self.inv.app_root) / directory
        if not root.is_dir():
            return []
        return sorted(
            f"{directory}/{p.name}".lstrip("/")
            for p in root.iterdir()
            if p.name.endswith(".go") and not p.name.endswith("_test.go") and p.is_file()
        )


@dataclass
class Resolved:
    file: str
    line: int
    end: int
    symbol: str
    recv: str | None
    literal: int | None = None


class Resolver:
    """``importpath:Func`` / ``Recv.Method`` / ``pkg.Recv.Method`` -> its one declaration."""

    def __init__(self, inv: Inventory, tree: GoTree) -> None:
        self.inv = inv
        self.tree = tree
        self.module = str(inv.extract.get("module") or "")
        self.external = 0
        self._seen_external: set[str] = set()
        self._cache: dict[tuple[str, str], Resolved | str] = {}

    def _dir(self, importpath: str) -> str | None:
        if importpath == self.module:
            rel = ""
        elif importpath.startswith(self.module + "/"):
            rel = importpath[len(self.module) + 1 :]
        else:
            return None
        return (
            self.inv.evidence_path(rel + "/x.go").rsplit("/", 1)[0]
            if rel
            else (self.inv.package.strip("/") if self.inv.package not in (".", "") else "")
        )

    def resolve(self, importpath: str, name: str) -> Resolved | str:
        """A :class:`Resolved`, or the reason it is not (``external`` | a sentence)."""
        key = (importpath, name)
        if key in self._cache:
            return self._cache[key]
        out = self._resolve(importpath, name)
        self._cache[key] = out
        return out

    def _resolve(self, importpath: str, name: str) -> Resolved | str:
        directory = self._dir(importpath)
        if directory is None:
            if f"{importpath}:{name}" not in self._seen_external:
                self._seen_external.add(f"{importpath}:{name}")
                self.external += 1
            return "external"
        literal: int | None = None
        m = _LITERAL.match(name)
        base = name
        if m:
            base, literal = m.group(1), int(m.group(2))
        recv: str | None = None
        fn = base
        if "." in base:
            recv, fn = base.rsplit(".", 1)
        hits: list[tuple[str, Decl]] = []
        for rel in self.tree.go_files(directory):
            for d in self.tree.decls(rel):
                if d.kind == "func" and d.name == fn and d.recv == recv:
                    hits.append((rel, d))
        if len(hits) == 1:
            rel, d = hits[0]
            return Resolved(rel, d.start, d.end, fn, recv, literal)
        if not hits:
            return f"no declaration of {base} in {directory or '.'}"
        return f"{len(hits)} declarations of {base} in {directory or '.'}"


# ---- the seeds ------------------------------------------------------------------------------


@dataclass
class RouteSeed:
    key: str
    rules: set[str] = field(default_factory=set)
    chains: set[str] = field(default_factory=set)
    targets: set[str] = field(default_factory=set)
    statuses: set[int] = field(default_factory=set)
    params: set[str] = field(default_factory=set)
    access: bool = False
    tables: set[str] = field(default_factory=set)


@dataclass
class TableSeed:
    table: str  # SQL name
    columns: set[str] = field(default_factory=set)
    rules: set[str] = field(default_factory=set)
    chains: set[str] = field(default_factory=set)
    targets: set[str] = field(default_factory=set)
    peers: bool = False  # a changed table: its peer routes too


@dataclass
class MachineSeed:
    table: str
    column: str
    states: set[str] = field(default_factory=set)
    rules: set[str] = field(default_factory=set)
    chains: set[str] = field(default_factory=set)
    targets: set[str] = field(default_factory=set)


def _json_keys(value: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(value, Mapping):
        for k, v in value.items():
            out.add(str(k))
            out |= _json_keys(v)
    elif isinstance(value, list | tuple):
        for v in value:
            out |= _json_keys(v)
    return out


def _step_params(step: Step) -> set[str]:
    out = _json_keys(step.json) | {str(k) for k in (step.params or {})}
    for entry in (step.expect_db or {}).values():
        if isinstance(entry, Mapping):
            out |= {str(k) for k in (entry.get("where") or {})}
            out |= {str(k) for k in (entry.get("equals") or {})}
    return out


def _step_tables(spec: Spec, step: Step) -> set[str]:
    from lattice.spec.model import resolve_table

    out: set[str] = set()
    for t in step.expect_db or {}:
        found = resolve_table(spec.profile, t)
        out.add(found[1] if found else t)
    return out


def _invariant_cols(
    spec: Spec, name: str, sql: Mapping[str, str]
) -> tuple[set[str], set[str], bool]:
    """(SQL tables, columns, whether it parsed) of an invariant's expression."""
    inv = next((i for i in spec.invariants if i.name == name), None)
    if inv is None:
        return set(), set(), False
    try:
        tree = ast.parse(inv.expr, mode="eval")
    except SyntaxError:
        return set(), set(), False
    tables: set[str] = set()
    cols: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in sql:
            tables.add(sql[n.value.id])
            cols.add(n.attr)
        elif isinstance(n, ast.Name) and n.id in sql:
            tables.add(sql[n.id])
        elif isinstance(n, ast.keyword) and n.arg == "by" and isinstance(n.value, ast.Constant):
            cols.add(str(n.value.value))
    return tables, cols, True


class Seeds:
    def __init__(self) -> None:
        self.routes: dict[str, RouteSeed] = {}
        self.tables: dict[str, TableSeed] = {}
        self.machines: dict[tuple[str, str], MachineSeed] = {}
        self.unread: list[dict[str, Any]] = []

    def route(self, key: str) -> RouteSeed:
        return self.routes.setdefault(key, RouteSeed(key))

    def table(self, name: str) -> TableSeed:
        return self.tables.setdefault(name, TableSeed(name))

    def machine(self, table: str, column: str) -> MachineSeed:
        return self.machines.setdefault((table, column), MachineSeed(table, column))


def _first_step(ctx: ImplContext, scenario: str, key: str) -> int | None:
    for i, k in enumerate(ctx.sv.scenarios.get(scenario, []), 1):
        if k == key:
            return i
    return None


def seed_rule(seeds: Seeds, ctx: ImplContext, rid: str, side: str) -> None:
    """Seeds of one rule through `ctx` (its elements, with the chain that reaches each)."""
    row = next((r for r in ctx.sv.rule_rows if r.id == rid), None)
    if row is None:
        return
    rule = f"rule:{rid}"
    cond = ctx.cv.conditional
    for link in row.links:
        kind, _, rest = link.partition(":")
        if kind == "scenario":
            for i, k in enumerate(ctx.sv.scenarios.get(rest, []), 1):
                if k in cond:
                    continue
                s = seeds.route(k)
                s.rules.add(rid)
                s.targets.add(link)
                s.chains.add(f"{rule} -> {link} / step {i} -> route:{k}")
        elif kind == "cell":
            from lattice.spec.implementation import _ROUTE

            for m, p in _ROUTE.findall(rest.split("|", 1)[-1]):
                k = route_key(m, p)
                s = seeds.route(k)
                s.rules.add(rid)
                s.targets.add(link)
                s.access = True
                s.chains.add(f"{rule} -> {link} -> route:{k}")
        elif kind == "machine":
            t, _, c = rest.partition(".")
            ms = seeds.machine(ctx.sv.sql(t), c)
            ms.rules.add(rid)
            ms.targets.add(link)
            ms.chains.add(f"{rule} -> {link}")
        elif kind == "profile":
            t, _, c = rest.partition(".")
            ts = seeds.table(ctx.sv.sql(t))
            ts.rules.add(rid)
            ts.targets.add(link)
            if c:
                ts.columns.add(c)
            ts.chains.add(f"{rule} -> {link}")
        elif kind == "invariant":
            tables, cols, ok = _invariant_cols(ctx.sv.spec, rest, ctx.sv.sql_names)
            if not ok:
                seeds.unread.append(
                    {"kind": "unparsable_invariant", "what": link, "reason": "does not parse"}
                )
            for t in sorted(tables):
                ts = seeds.table(t)
                ts.rules.add(rid)
                ts.targets.add(link)
                ts.columns |= cols
                ts.chains.add(f"{rule} -> {link}")
    for k in routes_in_text(row.text):
        s = seeds.route(k)
        s.rules.add(rid)
        s.chains.add(f"{rule} -> text -> route:{k}")
    del side


def _scenario_of(spec: Spec, name: str) -> Any:
    return next((s for s in spec.scenarios if s.name == name), None)


def seed_targets(
    seeds: Seeds,
    base: Spec,
    cand: Spec,
    targets: Mapping[str, list[str]],
    base_ctx: ImplContext | None,
    cand_ctx: ImplContext,
    cand_trace: Trace | None,
    base_trace: Trace | None,
) -> list[dict[str, Any]]:
    """Seeds of the changed spec elements. Returns ``response_shape_not_extracted`` unread
    items for changed body expectations."""
    unread: list[dict[str, Any]] = []

    def rules_of(trace: Trace | None, t: str) -> set[str]:
        return {r for r, ts in (trace.links if trace else {}).items() if t in ts}

    for how in ("added", "modified", "removed"):
        for t in targets.get(how, []):
            kind, _, name = t.partition(":")
            spec = base if how == "removed" else cand
            ctx = base_ctx if how == "removed" and base_ctx is not None else cand_ctx
            trace = base_trace if how == "removed" else cand_trace
            rules = rules_of(trace, t)
            if kind == "scenario":
                new = _scenario_of(cand, name)
                old = _scenario_of(base, name)
                pairs: list[tuple[Any, Any, int, Spec]] = []
                if how == "removed" and old is not None:
                    pairs = [(s, None, i, base) for i, s in enumerate(old.steps, 1)]
                elif new is not None:
                    for i, s in enumerate(new.steps, 1):
                        o = old.steps[i - 1] if old is not None and i <= len(old.steps) else None
                        pairs.append((s, o, i, cand))
                    if old is not None:
                        pairs += [
                            (s, None, i, base)
                            for i, s in enumerate(old.steps, 1)
                            if i > len(new.steps)
                        ]
                for step, other, i, sp in pairs:
                    if not isinstance(step, Step):
                        continue
                    if other is not None and step == other:
                        continue
                    k = route_key(step.method, step.path)
                    s = seeds.route(k)
                    s.rules |= rules
                    s.targets.add(t)
                    s.statuses.add(step.expect_status)
                    if isinstance(other, Step):
                        s.statuses.add(other.expect_status)
                    s.params |= _step_params(step)
                    if isinstance(other, Step):
                        s.params |= _step_params(other)
                    s.tables |= _step_tables(sp, step)
                    if step.expect_status in (401, 403, 404) or (
                        isinstance(other, Step) and other.expect_status in (401, 403, 404)
                    ):
                        s.access = True
                    s.chains.add(f"{t} / step {i} -> route:{k}")
                    if step.expect_body is not None and (
                        other is None
                        or not isinstance(other, Step)
                        or step.expect_body != other.expect_body
                    ):
                        unread.append(
                            {
                                "kind": "response_shape_not_extracted",
                                "what": f"{t} / step {i}",
                                "reason": f"the response body of {k} is not read from the code",
                                "file": None,
                                "line": None,
                            }
                        )
            elif kind == "invariant":
                tables, cols, ok = _invariant_cols(spec, name, ctx.sv.sql_names)
                if not ok:
                    unread.append(
                        {
                            "kind": "unparsable_invariant",
                            "what": t,
                            "reason": "does not parse",
                            "file": None,
                            "line": None,
                        }
                    )
                for tb in sorted(tables):
                    ts = seeds.table(tb)
                    ts.rules |= rules
                    ts.targets.add(t)
                    ts.columns |= cols
                    ts.peers = True
                    ts.chains.add(f"{t} -> table:{tb}")
            elif kind == "machine":
                tbl, _, col = name.partition(".")
                a = next(
                    (
                        m
                        for m in base.machines
                        if (m.column == col) and ctx.sv.sql(tbl) == ctx.sv.sql(m.table)
                    ),
                    None,
                )
                b = next(
                    (
                        m
                        for m in cand.machines
                        if (m.column == col) and ctx.sv.sql(tbl) == ctx.sv.sql(m.table)
                    ),
                    None,
                )
                ms = seeds.machine(ctx.sv.sql(tbl), col)
                ms.rules |= rules
                ms.targets.add(t)
                ms.chains.add(f"{t} -> machine writes")
                if a is not None and b is not None:
                    changed = set(a.edges) ^ set(b.edges)
                    ms.states |= {s for e in changed for s in e}
                    ms.states |= set(a.initial) ^ set(b.initial)
            elif kind == "profile":
                tbl, _, col = name.partition(".")
                ts = seeds.table(ctx.sv.sql(tbl))
                ts.rules |= rules
                ts.targets.add(t)
                ts.peers = True
                if how == "modified":
                    ts.columns |= _changed_columns(base, cand, tbl)
                ts.chains.add(f"{t} -> table:{ts.table}")
                if col:
                    ts.columns.add(col)
    return unread


def _changed_columns(base: Spec, cand: Spec, table: str) -> set[str]:
    from lattice.spec.incremental import spec_elements

    a = spec_elements(base).get(f"profile:{table}")
    b = spec_elements(cand).get(f"profile:{table}")
    if a is None or b is None:
        return set()
    out: set[str] = set()

    def walk(x: Any, y: Any, path: str) -> None:
        if isinstance(x, dict) and isinstance(y, dict):
            for k in sorted(set(x) | set(y)):
                if k not in x or k not in y:
                    out.add(k)
                else:
                    walk(x[k], y[k], k if not path else path)
        elif x != y and path:
            out.add(path)

    walk(a.get("schema"), b.get("schema"), "")
    return out


# ---- the locations --------------------------------------------------------------------------


class Locations:
    """Locations by (file, line, kind), merged: the strongest kind wins, vias are united."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, int], dict[str, Any]] = {}

    def add(
        self,
        kind: str,
        file: str,
        line: int,
        what: str,
        *,
        end: int | None = None,
        symbol: str | None = None,
        literal: int | None = None,
        rules: Iterable[str] = (),
        targets: Iterable[str] = (),
        chains: Iterable[str] = (),
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        key = (file, int(line))
        it = self.items.get(key)
        if it is None:
            it = self.items[key] = {
                "kind": kind,
                "what": what,
                "file": file,
                "line": int(line),
                "end_line": end,
                "symbol": symbol,
                "literal": literal,
                "rules": set(),
                "targets": set(),
                "via": set(),
                "whats": {what},
            }
        elif KINDS.index(kind) < KINDS.index(it["kind"]):
            it["kind"] = kind
            it["what"] = what
        it["whats"].add(what)
        if end and not it.get("end_line"):
            it["end_line"] = end
        if symbol and not it.get("symbol"):
            it["symbol"] = symbol
        if literal and not it.get("literal"):
            it["literal"] = literal
        it["rules"] |= set(rules)
        it["targets"] |= set(targets)
        it["via"] |= {f"{c} -> code:{file}:{line}" for c in chains}
        if extra:
            it.update(extra)


def _raw_routes(inv: Inventory) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in inv.extract.get("routes") or []:
        method = str(r.get("method") or "").upper()
        if method in ("HEAD", "OPTIONS"):
            continue
        path = str(r.get("path") or r.get("path_template") or "")
        methods = ("GET", "POST", "PUT", "PATCH", "DELETE") if method == "ANY" else (method,)
        for m in methods:
            out.setdefault(route_key(m, path), []).append(r)
    return out


def _is_plumbing(path: str) -> bool:
    return path in _PLUMBING or path.startswith("/debug/pprof/")


def _segments(key: str) -> list[str]:
    return [s for s in key.partition(" ")[2].split("/") if s]


def compute(
    inv: Inventory,
    base: Spec,
    cand: Spec,
    base_trace: Trace | None,
    cand_trace: Trace | None,
    diff: RuleDiff | None,
    targets: Mapping[str, list[str]],
    *,
    t1_routes: Sequence[tuple[str, str]] = (),
    cand_ctx: ImplContext | None = None,
    base_ctx: ImplContext | None = None,
) -> dict[str, Any]:
    """The Tier 3 section: ``locations``, ``flows``, ``unread``, ``external``, ``counts``.
    `t1_routes`: ``(route key, finding case)`` of the requests T1 saw fail."""
    cand_ctx = cand_ctx or ImplContext.of(cand, cand_trace, inv)
    if base_ctx is None and (diff is not None and diff.removed or targets.get("removed")):
        base_ctx = ImplContext.of(base, base_trace, inv)
    tree = GoTree(inv)
    resolver = Resolver(inv, tree)
    raw = _raw_routes(inv)
    cv = cand_ctx.cv
    seeds = Seeds()
    # (a) rules
    if diff is not None:
        for rid in diff.added:
            seed_rule(seeds, cand_ctx, rid, "new")
        for _o, n in diff.changed:
            seed_rule(seeds, cand_ctx, n, "new")
        if base_ctx is not None:
            for o, _n in diff.changed:
                seed_rule(seeds, base_ctx, o, "old")
            for rid in diff.removed:
                seed_rule(seeds, base_ctx, rid, "old")
    # (b) changed targets
    unread = seed_targets(seeds, base, cand, targets, base_ctx, cand_ctx, cand_trace, base_trace)
    unread.extend(seeds.unread)
    seeds.unread = []
    # (c) T1 failing steps
    for key, case in t1_routes:
        s = seeds.route(key)
        s.chains.add(f"check:{case} -> route:{key}")
    locs = Locations()
    # ---- route seeds
    seeded_flows: set[str] = set()
    missing: list[RouteSeed] = []
    for key in sorted(seeds.routes):
        s = seeds.routes[key]
        seeded_flows.add(key)
        regs = raw.get(key, [])
        if not regs:
            missing.append(s)
            continue
        rules, tg = sorted(s.rules), sorted(s.targets) or [f"route:{key}"]
        for r in regs:
            reg_file = inv.evidence_path(str(r["file"]))
            locs.add(
                "direct",
                reg_file,
                int(r["line"]),
                "route registration",
                rules=rules,
                targets=tg,
                chains=_pfx(s, "registered"),
            )
            h = r.get("handler") or {}
            if h.get("file"):
                hf = inv.evidence_path(str(h["file"]))
                lit = _LITERAL.match(str(h.get("func") or ""))
                if lit:
                    _enclose_literal(inv, tree, locs, h, int(lit.group(2)), rules, tg, s)
                else:
                    d = tree.enclosing(hf, int(h["line"]))
                    locs.add(
                        "direct",
                        hf,
                        int(h["line"]),
                        "handler declaration",
                        end=d.end if d else None,
                        symbol=_last(str(h.get("func") or "")),
                        rules=rules,
                        targets=tg,
                        chains=_pfx(s, f"handler:{h.get('func')}"),
                    )
            for rz in r.get("raises") or []:
                if s.statuses and rz.get("status") in s.statuses and rz.get("file"):
                    locs.add(
                        "direct",
                        inv.evidence_path(str(rz["file"])),
                        int(rz["line"]),
                        f"raise {rz.get('status')} {rz.get('code') or ''}".strip(),
                        rules=rules,
                        targets=tg,
                        chains=_pfx(s, f"raise:{rz.get('status')} {rz.get('code') or ''}".strip()),
                    )
            for p in r.get("params") or []:
                if p.get("name") in s.params and p.get("file"):
                    locs.add(
                        "direct",
                        inv.evidence_path(str(p["file"])),
                        int(p["line"]),
                        f"param {p['name']}",
                        rules=rules,
                        targets=tg,
                        chains=_pfx(s, f"param:{p['name']}"),
                    )
            if s.access:
                for ou in r.get("owner_uses") or []:
                    if ou.get("file"):
                        locs.add(
                            "direct",
                            inv.evidence_path(str(ou["file"])),
                            int(ou["line"]),
                            f"owner use {ou.get('table')}.{ou.get('column')}",
                            rules=rules,
                            targets=tg,
                            chains=_pfx(s, f"owner:{ou.get('table')}.{ou.get('column')}"),
                        )
            for call in r.get("calls") or []:
                imp, _, name = str(call).rpartition(":")
                res = resolver.resolve(imp, name)
                if isinstance(res, Resolved):
                    locs.add(
                        "closure",
                        res.file,
                        res.line,
                        "call declaration",
                        end=res.end,
                        symbol=res.symbol,
                        literal=res.literal,
                        rules=rules,
                        targets=tg,
                        chains=_pfx(s, f"call:{call}"),
                    )
                elif res != "external":
                    unread.append(
                        {
                            "kind": "unresolved_declaration",
                            "what": str(call),
                            "reason": res,
                            "file": None,
                            "line": None,
                        }
                    )
    # ---- table seeds
    for tname in sorted(seeds.tables):
        ts = seeds.tables[tname]
        t = cv.tables.get(tname)
        if t is None:
            unread.append(
                {
                    "kind": "seed_without_element",
                    "what": f"table:{tname}",
                    "reason": "no struct for the table in the extract",
                    "file": None,
                    "line": None,
                }
            )
            continue
        f = inv.evidence_path(str(t["file"]))
        rules, tg = sorted(ts.rules), sorted(ts.targets) or [f"table:{tname}"]
        chains = sorted(ts.chains) or [f"table:{tname}"]
        d = tree.enclosing(f, int(t["line"]))
        locs.add(
            "direct",
            f,
            int(t["line"]),
            "struct",
            end=d.end if d else None,
            symbol=str(t.get("struct") or ""),
            rules=rules,
            targets=tg,
            chains=[c + f" -> struct:{t.get('struct')}" for c in chains],
        )
        for col in t.get("columns") or []:
            if col.get("name") in ts.columns and col.get("line"):
                locs.add(
                    "direct",
                    f,
                    int(col["line"]),
                    f"column {col['name']}",
                    rules=rules,
                    targets=tg,
                    chains=[c + f" -> column:{col['name']}" for c in chains],
                )
    peer_tables = {name: seeds.tables[name] for name in seeds.tables if seeds.tables[name].peers}
    for tname, ts in sorted(peer_tables.items()):
        t = cv.tables.get(tname)
        if t is None:
            continue
        struct = str(t.get("struct") or "")
        for r in inv.extract.get("routes") or []:
            if struct not in (r.get("tables") or []) and tname not in (r.get("tables") or []):
                continue
            rules, tg = sorted(ts.rules), sorted(ts.targets)
            chain = sorted(ts.chains)[0] if ts.chains else f"table:{tname}"
            locs.add(
                "table_peer",
                inv.evidence_path(str(r["file"])),
                int(r["line"]),
                "route on a changed table",
                rules=rules,
                targets=tg,
                chains=[f"{chain} -> route:{r['method']} {r['path']}"],
            )
            h = r.get("handler") or {}
            if h.get("file"):
                hf = inv.evidence_path(str(h["file"]))
                d = tree.enclosing(hf, int(h["line"]))
                locs.add(
                    "table_peer",
                    hf,
                    int(h["line"]),
                    "handler on a changed table",
                    end=d.end if d else None,
                    symbol=_last(str(h.get("func") or "")),
                    rules=rules,
                    targets=tg,
                    chains=[
                        f"{chain} -> route:{r['method']} {r['path']} -> handler:{h.get('func')}"
                    ],
                )
    # ---- machine seeds
    wf_machines = inv.workflows["machines"] if inv.workflows else []
    for (tbl, col), ms in sorted(seeds.machines.items()):
        writes = [
            w
            for w in inv.extract.get("status_writes") or []
            if (w.get("table"), w.get("column")) == (tbl, col)
        ]
        chosen = [w for w in writes if not ms.states or str(w.get("value")) in ms.states] or writes
        for w in chosen:
            f = inv.evidence_path(str(w["file"]))
            d = tree.enclosing(f, int(w["line"]))
            locs.add(
                "direct",
                f,
                int(w["line"]),
                f"write of {tbl}.{col}",
                end=None if d is None else d.end,
                symbol=(d.name if d else None),
                rules=sorted(ms.rules),
                targets=sorted(ms.targets),
                chains=[c + f" -> status_write:{w.get('in')}" for c in sorted(ms.chains)],
            )
        for m in wf_machines:
            if m["table"] == tbl and m["column"] == col:
                for at in m["evidence"]:
                    mm = re.fullmatch(r"(.+):(\d+)", at)
                    if mm:
                        f = inv.evidence_path(mm.group(1))
                        locs.add(
                            "direct",
                            f,
                            int(mm.group(2)),
                            f"edge evidence {tbl}.{col}",
                            rules=sorted(ms.rules),
                            targets=sorted(ms.targets),
                            chains=[c + " -> machine edge" for c in sorted(ms.chains)],
                        )
        if not writes:
            unread.append(
                {
                    "kind": "seed_without_element",
                    "what": f"machine:{tbl}.{col}",
                    "reason": "no write of the machine's column in the extract",
                    "file": None,
                    "line": None,
                }
            )
    # ---- insertion points for endpoints the code lacks
    plain = [
        k
        for k, rs in raw.items()
        if not _is_plumbing(k.partition(" ")[2]) and not all(r.get("condition") for r in rs)
    ]
    common = _common_prefix([_segments(k) for k in plain])
    for s in missing:
        segs = _segments(s.key)
        best, sibs = 0, []
        for k in plain:
            n = _shared(segs, _segments(k))
            if n > best:
                best, sibs = n, [k]
            elif n == best and n:
                sibs.append(k)
        rules, tg = sorted(s.rules), sorted(s.targets) or [f"route:{s.key}"]
        if best >= len(common) + 1:
            for k in sorted(sibs):
                for r in raw[k]:
                    f = inv.evidence_path(str(r["file"]))
                    d = tree.enclosing(f, int(r["line"]))
                    line = d.start if d else int(r["line"])
                    locs.add(
                        "insertion",
                        f,
                        line,
                        "registering function of a sibling route",
                        end=d.end if d else None,
                        symbol=d.name if d else None,
                        rules=rules,
                        targets=tg,
                        chains=[
                            c.replace(f"route:{s.key}", f"route:{s.key} (absent)")
                            + f" -> sibling:{k}"
                            for c in (sorted(s.chains) or [f"route:{s.key}"])
                        ],
                    )
        else:
            for mf in inv.extract.get("main") or []:
                f = inv.evidence_path(str(mf))
                line = 1
                for dd in tree.decls(f):
                    if dd.name == "main" and dd.recv is None:
                        line = dd.start
                locs.add(
                    "insertion",
                    f,
                    line,
                    "main (no sibling route)",
                    rules=rules,
                    targets=tg,
                    chains=[c + " -> main" for c in (sorted(s.chains) or [f"route:{s.key}"])],
                )
        if not (best >= len(common) + 1) and not (inv.extract.get("main") or []):
            unread.append(
                {
                    "kind": "seed_without_element",
                    "what": f"route:{s.key}",
                    "reason": "not in the code and no insertion point",
                    "file": None,
                    "line": None,
                }
            )
        for tname, ts in sorted(seeds.tables.items()):
            if ts.rules & s.rules:
                t = cv.tables.get(tname)
                if t is not None:
                    f = inv.evidence_path(str(t["file"]))
                    d = tree.enclosing(f, int(t["line"]))
                    locs.add(
                        "insertion",
                        f,
                        int(t["line"]),
                        "struct of a linked table",
                        end=d.end if d else None,
                        symbol=str(t.get("struct") or ""),
                        rules=rules,
                        targets=tg,
                        chains=[f"rule -> table:{tname} -> struct:{t.get('struct')}"],
                    )
    # ---- finish
    out_locs = []
    for (f, line), it in sorted(locs.items.items()):
        item = {
            "tier": 3,
            "kind": it["kind"],
            "what": it["what"],
            "target": (sorted(it["targets"])[0] if it["targets"] else None),
            "rules": sorted(it["rules"], key=lambda r: (int(r[1:]) if r[1:].isdigit() else 0, r)),
            "file": f,
            "line": line,
            "symbol": it["symbol"],
            "via": sorted(it["via"])[:5],
            "evidence": [
                {"kind": "code", "at": f"code:{f}:{line}", "what": it["what"]},
                {"kind": "extract", "at": "extract.json", "what": sorted(it["whats"])[0]},
            ],
        }
        if it["end_line"]:
            item["end_line"] = it["end_line"]
        if it["literal"]:
            item["literal"] = it["literal"]
        out_locs.append(item)
    # extract unread inside reported ranges
    for u in inv.extract.get("unread") or []:
        uf = inv.evidence_path(str(u.get("file")))
        ul = int(u.get("line") or 0)
        for it in out_locs:
            hi = it.get("end_line") or it["line"]
            if it["file"] == uf and it["line"] <= ul <= hi:
                unread.append(
                    {
                        "kind": "extract_unread",
                        "what": str(u.get("what")),
                        "reason": str(u.get("reason")),
                        "file": uf,
                        "line": ul,
                    }
                )
                break
    unread.sort(
        key=lambda u: (
            str(u["kind"]),
            str(u.get("file")),
            int(u.get("line") or 0),
            str(u["what"]),
            str(u["reason"]),
        )
    )
    dedup: list[dict[str, Any]] = []
    for u in unread:
        if u not in dedup:
            dedup.append(u)
    flows = []
    if inv.workflows:
        for f in inv.workflows["flows"]:
            hit = sorted({s["route"] for s in f["steps"] if s["kind"] == "request"} & seeded_flows)
            if hit:
                flows.append(
                    {
                        "tier": 3,
                        "kind": "may_fail",
                        "target": f"flow:{f['id']}",
                        "rules": sorted(
                            {r for k in hit for r in seeds.routes[k].rules},
                            key=lambda r: (int(r[1:]) if r[1:].isdigit() else 0, r),
                        ),
                        "routes": hit,
                        "file": None,
                        "line": None,
                        "why_no_line": "a flow is a recorded request sequence, not a code location",
                        "evidence": [
                            {
                                "kind": "extract",
                                "at": f"flow:{f['id']}",
                                "what": "a step on a route the change touches: may fail, "
                                "not a claim",
                            }
                        ],
                    }
                )
    files = sorted({it["file"] for it in out_locs})
    by_kind = {k: sum(1 for it in out_locs if it["kind"] == k) for k in KINDS}
    return {
        "locations": out_locs,
        "flows": flows,
        "unread": dedup,
        "external": {"count": resolver.external},
        "files": files,
        "counts": {
            "locations": len(out_locs),
            "files": len(files),
            "by_kind": by_kind,
            "unread": len(dedup),
            "seeds": {
                "routes": len(seeds.routes),
                "tables": len(seeds.tables),
                "machines": len(seeds.machines),
            },
            "flows_may_fail": len(flows),
        },
        "stale": inv.stale is not None,
    }


def _enclose_literal(
    inv: Inventory,
    tree: GoTree,
    locs: Locations,
    h: Mapping[str, Any],
    literal: int,
    rules: list[str],
    targets: list[str],
    s: RouteSeed,
) -> None:
    """A handler that is a function literal (``X.funcN``): its enclosing declaration."""
    hf = inv.evidence_path(str(h["file"]))
    d = tree.enclosing(hf, int(h["line"]))
    if d is not None:
        locs.add(
            "direct",
            hf,
            d.start,
            "declaration enclosing a function literal",
            end=d.end,
            symbol=d.name,
            literal=literal,
            rules=rules,
            targets=targets,
            chains=_pfx(s, f"handler:{h.get('func')}"),
        )


def _pfx(s: RouteSeed, tail: str) -> list[str]:
    return [f"{c} -> {tail}" for c in (sorted(s.chains) or [f"route:{s.key}"])][:5]


def _last(func: str) -> str:
    return func.rsplit(".", 1)[-1] if func else ""


def _shared(a: Sequence[str], b: Sequence[str]) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


def _common_prefix(paths: Sequence[Sequence[str]]) -> list[str]:
    if not paths:
        return []
    out = list(paths[0])
    for p in paths[1:]:
        out = out[: _shared(out, p)]
    return out


def has_await(spec: Spec) -> bool:
    return any(isinstance(s, Await) for sc in spec.scenarios for s in sc.steps)


__all__ = ["compute", "template_path", "route_key"]
