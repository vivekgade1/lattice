"""The missing-index check of ``lattice perf --explain`` (pre-registration
``docs/results/2026-10-15-perf-missing-index-prereg.md``).

Statement counts cannot see a missing index (the count is the same, only the work per
statement grows), so the statements of the measured request are also *explained* on the
measured clone: after the size's last counting window, on a read-only connection, with
``EXPLAIN`` (never ``EXPLAIN ANALYZE``, nothing rewritten, nothing run). A table read by a full
scan under a ``WHERE`` that no index serves, holding at least ``min_rows`` rows, is a finding.

* **SQLite**: ``EXPLAIN QUERY PLAN`` on a ``mode=ro`` connection; ``SCAN <t>`` in a statement
  with a ``WHERE``, or any ``AUTOMATIC ... INDEX`` on ``<t>``. ``SEARCH`` is never a finding.
* **MySQL**: ``EXPLAIN FORMAT=TRADITIONAL`` in a read-only session with
  ``max_seeks_for_key = 1``; a row of type ``ALL`` or ``index`` whose ``Extra`` has
  ``Using where`` or ``Using join buffer``.
* **Postgres**: ``EXPLAIN (GENERIC_PLAN, FORMAT JSON)`` in a read-only session with
  ``enable_seqscan = off``; a ``Seq Scan`` with a ``Filter``, or an ``Index Scan`` /
  ``Index Only Scan`` with a ``Filter`` and no ``Index Cond``.

Plan *shape* only (access type, node, filter text), never estimates or costs: the result is
deterministic. The classifiers (:func:`sqlite_candidates`, :func:`mysql_candidates`,
:func:`pg_candidates`) are pure functions of a plan in the engine's documented format.

Every SQL this module sends is ``EXPLAIN ...``, a session ``SET`` or ``SELECT COUNT(*)``.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED = ("sqlite", "mysql", "postgres")
EXPLAINED_KINDS = ("SELECT", "WITH", "UPDATE", "DELETE")
# a test hook: statements appended to the list to explain (the P-safe probe injects a
# multi-statement text here to show it is skipped, never run)
EXTRA_STATEMENTS: list[str] = []

MULTI = "more than one statement"
UNRESOLVED = "alias not resolved"


@dataclass(frozen=True)
class ExplainOptions:
    """``min_rows``: the smallest table (its exact row count on the clone) that can be a
    finding; ``None``: the budget's largest measured size N (``0``: every table)."""

    min_rows: int | None = None


# ---- the statement text ----------------------------------------------------------------------


def _outside_semicolon(sql: str, backslash: bool) -> bool:
    """Whether ``sql`` has a ``;`` outside literals and quoted identifiers that is not the
    trailing one (a trailing ``;`` may be followed by spaces only)."""
    i, n = 0, len(sql)
    quote = ""
    while i < n:
        c = sql[i]
        if quote:
            if backslash and c == "\\" and quote != "`":
                i += 2
                continue
            if c == quote:
                if i + 1 < n and sql[i + 1] == quote:  # a doubled quote
                    i += 2
                    continue
                quote = ""
        elif c in "'\"`":
            quote = c
        elif c == ";" and sql[i + 1 :].strip():
            return True
        i += 1
    return False


def multi_statement(sql: str) -> bool:
    """Whether ``sql`` is (or may be) more than one statement. Read under both rules for a
    backslash in a literal (MySQL's escape; not the others'): either reading finding a ``;``
    outside a literal counts, so a mis-parse can only skip a statement, never run two."""
    return _outside_semicolon(sql, False) or _outside_semicolon(sql, True)


def first_keyword(sql: str) -> str:
    """The first keyword of ``sql``, upper case, after comments, spaces and ``(``."""
    s = sql
    while True:
        s = s.lstrip(" \t\r\n(")
        if s.startswith("--"):
            s = s.partition("\n")[2]
        elif s.startswith("/*"):
            s = s.partition("*/")[2]
        else:
            break
    m = re.match(r"[A-Za-z_]+", s)
    return m.group(0).upper() if m else ""


def _strip_trailing(sql: str) -> str:
    return sql.strip().rstrip(";").rstrip()


# ---- aliases -----------------------------------------------------------------------------------

_TOKEN = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`|[A-Za-z_][\w$]*|\d+|\S")
_NOT_ALIAS = {
    "WHERE", "ON", "USING", "LEFT", "RIGHT", "INNER", "OUTER", "CROSS", "NATURAL", "FULL",
    "JOIN", "STRAIGHT_JOIN", "GROUP", "ORDER", "LIMIT", "OFFSET", "HAVING", "UNION", "EXCEPT",
    "INTERSECT", "SET", "WINDOW", "FOR", "LOCK", "INDEXED", "NOT", "PARTITION", "USE", "FORCE",
    "IGNORE", "RETURNING", "VALUES", "SELECT", "WITH", "AS", "FROM", "INTO", "OR",
}  # fmt: skip


def _ident(tok: str) -> str:
    return tok[1:-1].replace(tok[0] * 2, tok[0]) if tok and tok[0] in '"`' else tok


def alias_map(sql: str) -> dict[str, str]:
    """``{alias (lower case): table}`` from ``(FROM|JOIN) <table> [AS] <alias>`` (comma
    lists included) and ``UPDATE <table> [AS] <alias>``. A table written qualified keeps its
    last part; a derived table has no entry (its name is then not resolved)."""
    toks = _TOKEN.findall(sql)
    out: dict[str, str] = {}
    n = len(toks)

    def ref(i: int) -> int:
        """Read one table reference at ``i``; the index after it."""
        if i >= n or not re.match(r"[A-Za-z_\"`]", toks[i]) or toks[i].upper() in _NOT_ALIAS:
            return i + 1 if i < n and toks[i] == "(" else i
        name = _ident(toks[i])
        i += 1
        while i + 1 < n and toks[i] == "." and re.match(r"[A-Za-z_\"`]", toks[i + 1]):
            name = _ident(toks[i + 1])
            i += 2
        if i < n and toks[i].upper() == "AS" and i + 1 < n:
            i += 1
        if i < n and re.match(r"[A-Za-z_\"`]", toks[i]) and toks[i].upper() not in _NOT_ALIAS:
            out[_ident(toks[i]).lower()] = name
            i += 1
        return i

    i = 0
    while i < n:
        word = toks[i].upper()
        if word in ("FROM", "JOIN"):
            i = ref(i + 1)
            while i < n and toks[i] == ",":
                i = ref(i + 1)
            continue
        if word == "UPDATE":
            j = i + 1
            if j + 1 < n and toks[j].upper() == "OR":
                j += 2
            i = ref(j)
            continue
        i += 1
    return out


# ---- the plans: pure classifiers ----------------------------------------------------------------

_SQLITE_SCAN = re.compile(r"^SCAN (?:TABLE )?(\S+?)(?: AS (\S+))?(?: USING .*)?$")
_SQLITE_AUTO = re.compile(r"^SEARCH (?:TABLE )?(\S+?)(?: AS (\S+))? USING AUTOMATIC ")


def sqlite_candidates(shape_sql: str, details: Iterable[str]) -> list[tuple[str, str]]:
    """``(plan name, access)`` of the plan rows that read a table without an index: ``SCAN``
    in a statement that has a ``WHERE``, and any ``AUTOMATIC [PARTIAL] [COVERING] INDEX``."""
    has_where = re.search(r"\bWHERE\b", shape_sql, re.I) is not None
    out: list[tuple[str, str]] = []
    for detail in details:
        d = detail.strip()
        auto = _SQLITE_AUTO.match(d)
        if auto:
            out.append((auto.group(2) or auto.group(1), d))
            continue
        scan = _SQLITE_SCAN.match(d)
        if scan and has_where and scan.group(1) not in ("CONSTANT",):
            out.append((scan.group(2) or scan.group(1), d))
    return out


def mysql_candidates(rows: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """``(table, access)`` of the ``EXPLAIN FORMAT=TRADITIONAL`` rows of type ``ALL`` or
    ``index`` whose ``Extra`` has ``Using where`` or ``Using join buffer``; derived
    (``<derivedN>``, ``<subqueryN>``, ``<unionM,N>``) and absent tables are not candidates."""
    out: list[tuple[str, str]] = []
    for row in rows:
        r = {str(k).lower(): v for k, v in row.items()}
        table, kind = r.get("table"), r.get("type")
        extra = str(r.get("extra") or "")
        if table is None or str(table).startswith("<") or kind is None:
            continue
        if str(kind) not in ("ALL", "index"):
            continue
        if "Using where" not in extra and "Using join buffer" not in extra:
            continue
        key = r.get("key")
        out.append((str(table), f"type={kind} key={'NULL' if key is None else key} Extra={extra}"))
    return out


def pg_candidates(plan: Any) -> list[tuple[str, str]]:
    """``(relation, access)`` of the nodes of an ``EXPLAIN (FORMAT JSON)`` plan that scan
    a relation fully under a filter: ``Seq Scan`` with a ``Filter``; ``Index Scan`` and
    ``Index Only Scan`` with a ``Filter`` and no ``Index Cond`` (with ``enable_seqscan = off``
    the planner reads the primary key in full to satisfy an ``ORDER BY``)."""
    out: list[tuple[str, str]] = []

    def walk(node: Mapping[str, Any]) -> None:
        kind = node.get("Node Type")
        rel = node.get("Relation Name")
        filt = node.get("Filter")
        if rel is not None and filt is not None:
            full = kind == "Seq Scan" or (
                kind in ("Index Scan", "Index Only Scan") and node.get("Index Cond") is None
            )
            if full:
                out.append((str(rel), f"{kind} on {rel}, Filter: {filt}"))
        for child in node.get("Plans") or ():
            walk(child)

    if isinstance(plan, str):
        plan = json.loads(plan)
    for top in plan if isinstance(plan, list) else [plan]:
        walk(top.get("Plan", top))
    return out


# ---- the engines ---------------------------------------------------------------------------------


class SqliteExplainer:
    """``EXPLAIN QUERY PLAN`` on the measured file through a read-only connection (not the
    watched path as written, so the counter's trace never sees it)."""

    engine = "sqlite"

    def __init__(self, path: Path | str) -> None:
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True)

    def plan(self, sql: str) -> list[str]:
        text = "EXPLAIN QUERY PLAN " + _strip_trailing(sql)
        try:
            cur = self.conn.execute(text)
        except sqlite3.ProgrammingError as err:  # an unexpanded ? : bind NULLs
            m = re.search(r"statement uses (\d+)", str(err))
            if m is None:
                raise
            cur = self.conn.execute(text, (None,) * int(m.group(1)))
        return [str(r[3]) for r in cur.fetchall()]

    def candidates(self, shape_sql: str, plan: list[str]) -> list[tuple[str, str]]:
        return sqlite_candidates(shape_sql, plan)

    def count(self, table: str) -> int:
        quoted = '"' + table.replace('"', '""') + '"'
        return int(self.conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])

    def close(self) -> None:
        self.conn.close()


class _ServerExplainer:
    engine = ""
    session: tuple[str, ...] = ()

    def __init__(self, url: str) -> None:
        from lattice.spec.db import open_db

        self.db = open_db(url)
        for text in self.session:
            self.db.execute(text)

    def count(self, table: str) -> int:
        return int(self.db.scalar(f"SELECT COUNT(*) FROM {self.db.quote_ident(table)}"))

    def close(self) -> None:
        self.db.close()


class MysqlExplainer(_ServerExplainer):
    engine = "mysql"
    session = ("SET SESSION TRANSACTION READ ONLY", "SET SESSION max_seeks_for_key = 1")

    def plan(self, sql: str) -> list[dict[str, Any]]:
        return self.db.query_dicts("EXPLAIN FORMAT=TRADITIONAL " + _strip_trailing(sql))

    def candidates(self, shape_sql: str, plan: Any) -> list[tuple[str, str]]:
        return mysql_candidates(plan)


class PostgresExplainer(_ServerExplainer):
    engine = "postgres"
    session = (
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
        "SET statement_timeout = '5s'",
        "SET enable_seqscan = off",
    )

    def plan(self, sql: str) -> Any:
        rows = self.db.query("EXPLAIN (GENERIC_PLAN, FORMAT JSON) " + _strip_trailing(sql))
        return rows[0][0]

    def candidates(self, shape_sql: str, plan: Any) -> list[tuple[str, str]]:
        return pg_candidates(plan)


def open_explainer(engine: str, ref: Any) -> Any:
    """The read-only explain connection to the measured clone ``ref`` (a SQLite path or a
    server URL) of ``engine``."""
    if engine == "sqlite":
        return SqliteExplainer(ref)
    if engine == "mysql":
        return MysqlExplainer(str(ref))
    if engine == "postgres":
        return PostgresExplainer(str(ref))
    raise ValueError(f"explain is not supported on {engine}")


# ---- the check ----------------------------------------------------------------------------------


def _result(size: int | None, engine: str, status: str, reason: str, min_rows: int,
            **rest: Any) -> dict[str, Any]:  # fmt: skip
    return {
        "size": size, "engine": engine, "status": status, "reason": reason,
        "min_rows": min_rows, "explained": rest.get("explained", 0),
        "ignored": rest.get("ignored", 0), "below_threshold": rest.get("below_threshold", 0),
        "skipped": rest.get("skipped", []), "tables": rest.get("tables", {}),
        "findings": rest.get("findings", []),
    }  # fmt: skip


def unchecked(reason: str, engine: str = "", size: int | None = None, min_rows: int = 0) -> dict:
    """An explain object of a budget that was not (or cannot be) checked."""
    return _result(size, engine, "unchecked", reason, min_rows)


def explain_statements(
    engine: str, ref: Any, statements: Sequence[str], n: int, min_rows: int | None = None,
    open_fn: Callable[[str, Any], Any] | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The explain object (see the module docstring and the pre-registration, decision 7) of
    ``statements`` (a run's raw statements) on the clone ``ref`` of size ``n``."""
    from lattice.perf.measure import shape

    threshold = n if min_rows is None else min_rows
    conn = (open_fn or open_explainer)(engine, ref)
    try:
        seen: dict[str, str] = {}
        for sql in [*statements, *EXTRA_STATEMENTS]:
            seen.setdefault(shape(sql), sql)
        ignored = 0
        explained = 0
        below = 0
        skipped: list[dict[str, str]] = []
        tables: dict[str, int | None] = {}
        findings: dict[tuple[str, str], dict[str, Any]] = {}
        candidates_total = 0
        for shp, sql in seen.items():
            if first_keyword(sql) not in EXPLAINED_KINDS:
                ignored += 1
                continue
            candidates_total += 1
            if multi_statement(sql):
                skipped.append({"sql": shp, "reason": MULTI})
                continue
            try:
                plan = conn.plan(sql)
            except Exception as err:  # noqa: BLE001 -- the engine refused: skip with its words
                line = (str(err).strip().splitlines() or [type(err).__name__])[0]
                skipped.append({"sql": shp, "reason": f"EXPLAIN failed: {line[:200]}"})
                continue
            aliases = alias_map(sql)
            unresolved: list[str] = []
            for name, access in conn.candidates(shp, plan):
                table = aliases.get(name.lower(), name)
                if table not in tables:
                    try:
                        tables[table] = conn.count(table)
                    except Exception:  # noqa: BLE001 -- not a table of the clone
                        tables[table] = None
                rows = tables[table]
                if rows is None:
                    unresolved.append(name)
                    continue
                if rows < threshold:
                    below += 1
                    continue
                findings.setdefault(
                    (table, shp), {"table": table, "rows": rows, "access": access, "sql": shp}
                )
            if unresolved:
                skipped.append({"sql": shp, "reason": f"{UNRESOLVED}: {', '.join(unresolved)}"})
            else:
                explained += 1
    finally:
        with contextlib.suppress(Exception):
            conn.close()
    found = [findings[k] for k in sorted(findings)]
    status, reason = "checked", ""
    if candidates_total and explained == 0:
        status, reason = "unchecked", skipped[0]["reason"] if skipped else "nothing explained"
    elif skipped:
        status, reason = "partial", f"{len(skipped)} statement(s) not explained"
    return _result(
        n, engine, status, reason, threshold, explained=explained, ignored=ignored,
        below_threshold=below, skipped=sorted(skipped, key=lambda s: (s["sql"], s["reason"])),
        tables={k: v for k, v in sorted(tables.items()) if v is not None}, findings=found,
    )  # fmt: skip


__all__ = [
    "EXPLAINED_KINDS",
    "EXTRA_STATEMENTS",
    "SUPPORTED",
    "ExplainOptions",
    "MysqlExplainer",
    "PostgresExplainer",
    "SqliteExplainer",
    "alias_map",
    "explain_statements",
    "first_keyword",
    "multi_statement",
    "mysql_candidates",
    "open_explainer",
    "pg_candidates",
    "sqlite_candidates",
    "unchecked",
]
