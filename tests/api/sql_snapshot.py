"""The compiled SQL of a representative set of plans, for the dialect-refactor snapshot.

`collect()` compiles every tabular case of tests/api/test_sql.py (through
`rels_from_plan` + `compile_select`) and lowers every reference API program
(tests/fixtures/api/*/lattice.py and the prompt's worked examples), returning each
statement as a JSON-able record. `tests/fixtures/sql_snapshot.json` holds the output
taken before the SQL layer was parameterized by a dialect; test_backend.py asserts the
SQLite dialect still produces it byte for byte.

Regenerate (only when the SQL is meant to change):
    uv run python -m tests.api.sql_snapshot
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SNAPSHOT = FIXTURES / "sql_snapshot.json"


def _record(sql: Any) -> dict[str, Any]:
    return {
        "text": sql.text,
        "params": list(sql.params),
        "binds": [[n, v] for n, v in sql.binds],
        "rows": [[b, s, list(c)] for b, s, c in sql.rows],
        "user": sql.user,
    }


def _tabular(compile_select: Any, dialect: Any = None) -> dict[str, Any]:
    from lattice.api.sql import rels_from_plan
    from lattice.check import check_source
    from lattice.ir import lower
    from tests.api.test_sql import CASES, LOAD, PROFILE, TABLES, program

    out: dict[str, Any] = {}
    for name in sorted(CASES):
        body = LOAD + CASES[name] + '\nwrite(x, "out.csv")\n'
        check = check_source(program(body), PROFILE)
        if check.program is None:  # e.g. the nested-call "constants" spelling
            continue
        plan = lower(check.program)
        written = next(n for n in plan.nodes if n.op == "write")
        rels = rels_from_plan(plan.nodes, TABLES)
        kw = {} if dialect is None else {"dialect": dialect}
        out[f"tabular/{name}"] = _record(compile_select(rels, written.inputs[0], **kw))
    return out


def _api(lower_api: Any) -> dict[str, Any]:
    from bench.api_datasets import api_profile
    from lattice.check import check_source
    from lattice.llm.api_prompt import LATTICE_EXAMPLES

    sources = {
        f"api/{p.parent.name}": p.read_text()
        for p in sorted((FIXTURES / "api").glob("*/lattice.py"))
    }
    sources |= {f"api/example{i}": s for i, s in enumerate(LATTICE_EXAMPLES, 1)}
    out: dict[str, Any] = {}
    for key, source in sources.items():
        check = check_source(source, api_profile())
        assert check.program is not None, key
        for plan in lower_api(check.program):
            for step in plan.steps:
                sql = getattr(step, "sql", None)
                if sql is not None and sql.text:
                    out[f"{key}/{plan.name}/{step.id}"] = _record(sql)
    return out


def collect(dialect: Any = None) -> dict[str, Any]:
    """Every statement (tabular cases compiled with `dialect` when given)."""
    from lattice.api.ir import lower_api
    from lattice.api.sql import compile_select

    return _tabular(compile_select, dialect) | _api(lower_api)


if __name__ == "__main__":
    SNAPSHOT.write_text(json.dumps(collect(), indent=1, sort_keys=True) + "\n")
    print(f"wrote {SNAPSHOT}")
