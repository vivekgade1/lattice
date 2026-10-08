"""Authoring helpers that make specs cheaper to write and review (roadmap: specs are
~9k tokens for a small app, much of it hand-written role x action access steps).

* :func:`access_matrix` generates :class:`~lattice.spec.model.Scenario` objects from a
  compact "who can do what" table, instead of one hand-written `Scenario` per
  (caller, action) pair.
* :func:`requirements_markdown` renders a `Spec` as deterministic prose -- the spec
  stays the single source; the markdown is a view onto it, for humans or as a
  baseline to diff against a hand-written requirements document.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from lattice.spec.model import Scenario, Spec, Step, tables_of
from lattice.typesys import Schema

__all__ = ["access_matrix", "requirements_markdown"]

# ---- access_matrix ------------------------------------------------------------------------

# One row of the ``actions`` table: method, path, JSON body (or None), and an optional
# count check (table, delta) -- the row count a successful call changes by `delta`; a
# refused call leaves the table's row count exactly where `baseline` says it was.
ActionSpec = tuple[str, str, "Mapping[str, Any] | None", "tuple[str, int] | None"]

_MISSING = object()


def _cell(value: Any) -> tuple[int, Any, Any]:
    """(status, body, expect_db) from a compact cell: a bare status, or a tuple
    ``(status,)``, ``(status, body)`` or ``(status, body, expect_db)``. `body` and
    `expect_db` are `_MISSING` when the cell does not give them -- the caller falls
    back to `answers` (for the body) or the action's own count check (for `expect_db`);
    an explicit ``None`` for either means "no check", not "fall back"."""
    if isinstance(value, bool):
        raise ValueError(f"a caller cell must be a status or a tuple, not a bool: {value!r}")
    if isinstance(value, int):
        return value, _MISSING, _MISSING
    if isinstance(value, tuple) and 1 <= len(value) <= 3:
        padded = value + (_MISSING,) * (3 - len(value))
        return padded  # type: ignore[return-value]
    raise ValueError(
        f"a caller cell must be a status, or (status[, body[, expect_db]]); got {value!r}"
    )


def _caller_label(caller: str | None) -> str:
    return caller if caller is not None else "anon"


def _auto_expect_db(
    matrix: str,
    action: str,
    count_check: tuple[str, int] | None,
    status: int,
    baseline: Mapping[str, int],
) -> Mapping[str, Any] | None:
    """The automatic `expect_db` for one cell: `None` when the action has no count
    check; else the table's absolute row count after this call -- `baseline[table] +
    delta` for a successful call (2xx), `baseline[table]` unchanged for a refused one
    (the write did nothing)."""
    if count_check is None:
        return None
    table, delta = count_check
    if table not in baseline:
        raise ValueError(
            f"access_matrix {matrix!r}: action {action!r} checks {table!r}'s row count; "
            f"give baseline={{{table!r}: <its count right after setup>, ...}}"
        )
    base = baseline[table]
    after = base + delta if 200 <= status < 300 else base
    return {table: {"count": after}}


def access_matrix(
    name: str,
    *,
    actions: Mapping[str, ActionSpec],
    callers: Mapping[str | None, Mapping[str, Any]],
    answers: Mapping[int, Any] | None = None,
    setup: Sequence[Step] = (),
    baseline: Mapping[str, int] | None = None,
    given: Mapping[str, list[dict[str, Any]]] | None = None,
) -> tuple[Scenario, ...]:
    """Generate one `Scenario` per (caller, action) cell of a role x action access
    table, instead of one hand-written per pair.

    `actions`: name -> (method, path, json body or None, count check or None). The
    count check (table, delta) says a successful call changes that table's row count
    by `delta`; `baseline` must then give the table's row count right after `setup`
    runs (the count every cell's call starts from, since each cell runs on a fresh
    seed). A refused call (a non-2xx status) is checked to have changed nothing: the
    table's count stays at `baseline[table]`.

    `callers`: caller handle (or `None` for no caller) -> {action name: cell}. A cell
    is a bare HTTP status, or a tuple `(status,)`, `(status, body)` or `(status, body,
    expect_db)`. `body` falls back to `answers.get(status)` (a default body per status,
    e.g. `{403: DENIED, 404: NOT_FOUND}`) when omitted; an explicit cell `expect_db`
    overrides the action's automatic count check for that one cell (pass `None`
    explicitly to skip checking the database for that cell). A caller's dict need not
    cover every action -- only the cells that caller/action pair is meant to check.

    `setup`: steps run (on the caller named in each step) before every cell's own step,
    on that cell's fresh seeded database -- the state every cell's call sees. `given`:
    extra seed rows, as `Scenario.given`, shared by every generated scenario.

    Generated scenario names are deterministic and readable:
    ``access__<action>__as_<caller|anon>``. Raises `ValueError` for an unknown action
    name, a malformed cell, or a count check whose table has no `baseline` entry.
    """
    setup = tuple(setup)
    answers = dict(answers or {})
    baseline = dict(baseline or {})
    scenarios: list[Scenario] = []
    seen: set[str] = set()
    for caller, cells in callers.items():
        for action, cell in cells.items():
            if action not in actions:
                known = ", ".join(sorted(actions))
                raise ValueError(
                    f"access_matrix {name!r}: caller {caller!r} names unknown action "
                    f"{action!r}; known actions: {known}"
                )
            method, path, json_body, count_check = actions[action]
            status, body, expect_db = _cell(cell)
            if body is _MISSING:
                body = answers.get(status)
            if expect_db is _MISSING:
                expect_db = _auto_expect_db(name, action, count_check, status, baseline)
            step = Step(
                method,
                path,
                caller,
                json=json_body,
                expect_status=status,
                expect_body=body,
                expect_db=expect_db,
            )
            scenario_name = f"access__{action}__as_{_caller_label(caller)}"
            if scenario_name in seen:
                raise ValueError(
                    f"access_matrix {name!r}: two cells both produce scenario "
                    f"{scenario_name!r}"
                )
            seen.add(scenario_name)
            scenarios.append(Scenario(scenario_name, given=given, steps=(*setup, step)))
    return tuple(scenarios)


# ---- requirements_markdown -----------------------------------------------------------------


def _short(value: Any, limit: int = 140) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _column_line(schema: Schema, name: str, single_keys: set[str]) -> str:
    scalar = schema.get(name)
    tags = []
    if name in single_keys:
        tags.append("key")
    if name in schema.nullable:
        tags.append("nullable")
    if name in schema.private:
        tags.append("private")
    choices = schema.choices(name)
    if choices:
        tags.append("one of " + ", ".join(choices))
    tag_text = f" ({'; '.join(tags)})" if tags else ""
    kind = scalar.value if scalar is not None else "?"
    return f"- {name}: {kind}{tag_text}"


def _table_block(schema_name: str, sql_name: str, schema: Schema) -> list[str]:
    heading = f"### {schema_name}" + (f" ({sql_name})" if sql_name != schema_name else "")
    single_keys = {k[0] for k in schema.keys if len(k) == 1}
    multi_keys = [k for k in schema.keys if len(k) > 1]
    lines = [heading, ""]
    lines += [_column_line(schema, n, single_keys) for n, _ in schema.columns]
    lines += [f"- key: ({', '.join(k)})" for k in multi_keys]
    lines.append("")
    return lines


def _tables_block(spec: Spec) -> list[str]:
    tables, sql_names = tables_of(spec.profile)
    if not tables:
        return []
    lines = ["## Tables", ""]
    for schema_name, schema in tables.items():
        lines += _table_block(schema_name, sql_names.get(schema_name, schema_name), schema)
    return lines


def _invariants_block(spec: Spec) -> list[str]:
    if not spec.invariants:
        return []
    lines = ["## Invariants", ""]
    lines += [f"- **{inv.name}**: {inv.sentence}" for inv in spec.invariants]
    lines.append("")
    return lines


def _machines_block(spec: Spec) -> list[str]:
    if not spec.machines:
        return []
    lines = ["## State machines", ""]
    for m in spec.machines:
        moves: dict[str, list[str]] = {}
        for src, dst in m.edges:
            moves.setdefault(src, []).append(dst)
        parts = [f"{src} -> {', '.join(dsts)}" for src, dsts in moves.items()]
        initial = f" (initial: {', '.join(m.initial)})" if m.initial else ""
        lines.append(f"- **{m.table}.{m.column}**: " + "; ".join(parts) + initial)
    lines.append("")
    return lines


def _db_fact(table: str, cond: Mapping[str, Any]) -> str:
    where = cond.get("where")
    where_text = f" where {where}" if where else ""
    if "count" in cond:
        return f"{table}{where_text} count={cond['count']}"
    return f"{table}{where_text} equals {cond.get('equals')}"


def _step_fact(step: Step) -> str:
    bits = []
    if step.expect_body is not None:
        bits.append(f"body {_short(step.expect_body)}")
    if step.expect_db:
        bits.append("; ".join(_db_fact(t, c) for t, c in step.expect_db.items()))
    return "; ".join(bits)


def _scenarios_block(spec: Spec) -> list[str]:
    if not spec.scenarios:
        return []
    lines = ["## Scenarios", ""]
    for scenario in spec.scenarios:
        lines.append(f"- **{scenario.name}**")
        for step in scenario.steps:
            who = step.user or "anon"
            fact = _step_fact(step)
            tail = f" -- {fact}" if fact else ""
            lines.append(f"  - {step.method} {step.path} as {who} -> {step.expect_status}{tail}")
    lines.append("")
    return lines


def _unchecked_block(spec: Spec) -> list[str]:
    if not spec.unchecked:
        return []
    lines = ["## Not checked", ""]
    for u in spec.unchecked:
        concerns = f" ({', '.join(u.concerns)})" if u.concerns else ""
        lines.append(f"- {u.text}{concerns}")
    lines.append("")
    return lines


def requirements_markdown(spec: Spec, *, title: str, notes: Sequence[str] = ()) -> str:
    """Render `spec` as prose requirements: its tables and columns (one_of values,
    keys, nullable and private columns), its invariants as their sentences, its
    machines as their allowed moves, each scenario as a short bullet (the method, path
    and caller of each step, then the status and key facts -- the expected body and
    database checks), and the unchecked notes.

    Deterministic: the same `spec` always renders the same text (the field order of
    `Spec.profile`'s tables, and the tuple order of invariants/machines/scenarios/
    unchecked, decide the output order), so the spec stays the single source -- this is
    a view onto it, for humans or as a baseline to diff against a hand-written
    requirements document.
    """
    lines = [f"# {title}", ""]
    lines += _tables_block(spec)
    lines += _invariants_block(spec)
    lines += _machines_block(spec)
    lines += _scenarios_block(spec)
    lines += _unchecked_block(spec)
    if notes:
        lines.append("## Notes")
        lines.append("")
        lines += [f"- {n}" for n in notes]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
