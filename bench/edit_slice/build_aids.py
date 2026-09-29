"""Build the "common mistakes -> exact fix" tables of arms L+ and Q+ (round-5 spec §7 (b))
from the round-4 artefacts, deterministically, into :data:`bench.edit_slice.aids.TABLE_FILES`.

The generated files are committed: round 5 uses that fixed version, never a rebuild.

Both tables follow one rule (round-5 review C1): **the evidence only ranks codes, and
every row is its code's generic, ticket-neutral text** (:data:`GENERIC_ROWS`) -- no
route path, function, table or column name of any app or ticket, only placeholders
(``<Table>``, ``<column>``, ...). And **no evidence is selected by the hidden oracle**:

* **L+ (Lattice)** -- the C3 repair-persistence analysis
  (:mod:`bench.analysis.diag_persistence`) of arm L's checker refusals in round 4 (the
  rounds' own feedback). Codes are ranked by how often a refusal *repeated* in the next
  round (the mistakes repair feedback alone did not fix), then by persistence, then by
  refused rounds; a code today's checker no longer reports on the saved files is
  skipped.
* **Q+ (Python)** -- the final round of *every* round-4 arm-P run, whatever the oracle
  said (read from the saved artefacts only, never the results JSONL that holds the
  verdicts), checked by pycheck with arm Q's policy
  (:func:`bench.edit_slice.qcheck.check_files`). Codes are ranked by the number of runs
  they flag.

Both tables are capped at the same length, in characters of the rendered section
(:func:`bench.edit_slice.aids.render_table`): the budget is the smaller of the two full
tables (each at most :data:`MAX_ROWS` rows), and each table takes its rows in rank order
while it fits. Every cell is at most :data:`CELL_CHARS` characters.

``uv run python -m bench.edit_slice.build_aids [--check]`` (``--check``: exit 1 if the
committed files differ from a rebuild).
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import types
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from bench.edit_slice.aids import TABLE_FILES, MistakeRow, render_table

ROOT = Path(__file__).resolve().parents[2]
RESULTS = "bench/results/edit_slice"
L_PATTERN = f"{RESULTS}/round4_*_L.jsonl"
P_PATTERN = f"{RESULTS}/round4_*_P.jsonl"
MAX_ROWS = 8
CELL_CHARS = 280


def cap(text: str, limit: int = CELL_CHARS) -> str:
    """``text`` on one line, cut at a word boundary to at most ``limit`` characters."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 2].rsplit(" ", 1)[0]
    return cut + " …"


# code -> (mistake, exact fix): one ticket-neutral row per code. Written from the checker's
# (L) and pycheck's (PY) own message and hint for the code, every app-specific identifier
# replaced by a placeholder: <Table>, <Parent>, <column>, <owner column>, <name>, <row>,
# <param>, <procedure>, <caller>, <admin check>, <datetime>.
GENERIC_ROWS: dict[str, tuple[str, str]] = {
    # ---- Lattice checker codes (L+) ----
    "L146": (
        "'raise' is not allowed in a procedure",
        "return a value that signals the failure (e.g. None) and let the endpoint answer",
    ),
    "L302": (
        "each statement must call exactly one table op or step (or bind a constant that "
        "uses no columns)",
        "split it into one op per statement, e.g. '<name> = derive(<table>, <new_col>=...)'",
    ),
    "L401": (
        'col("<column>") has no input rows here (insert() values, constants and call() '
        "arguments read no table)",
        "read the value of the one row that holds it: <row>.<column>",
    ),
    "L402": ("'|' cannot combine Str and Bool", "both sides must be Bool comparisons"),
    "L404": (
        "accessible() needs rows of a table with an owner column; the profile declares none "
        "for <Table>, so its rows are shared",
        "if each <Table> row belongs to the user in its <owner column>, declare it in "
        "profile.py: add owner='<owner column>' to <Table>'s Schema.from_pairs(...) (then "
        "every endpoint that returns or writes its rows filters them with accessible(..., "
        "by=user)); otherwise drop accessible()",
    ),
    "L411": (
        "'<name>' is a table, not a value",
        'reference a column with col("<column>"); pass tables to ops by name',
    ),
    "L416": (
        "column '<column>' exists in both tables; rename one side first",
        'e.g. rename(<table>, <column>="<column>_2")',
    ),
    "L439": (
        "call() hands '<name>' to a procedure, but it may have any number of rows",
        "pass rows of one(...), page(...) or limit(<table>, n=N) with N <= 100",
    ),
    "L601": (
        "parameter '<param>' needs an API parameter type",
        'use Int(ge=, le=), Float(ge=, le=), Str(min_len=, max_len=, pattern=), OneOf("a", '
        '"b"), Email(max_len=), Bool, Date, DateTime, Uuid (or a bare Int/Float/Str/Email), '
        "or user: CurrentUser",
    ),
    "L803": (
        "where() reads the private column '<column>'; a Customer endpoint's response would "
        "reveal its values",
        "only Admin endpoints may filter on a private column. If it is not secret (it only "
        "has to stay out of responses), declare it in profile.py without private: select() "
        "and the response model keep it out of responses",
    ),
    "L883": (
        "'<procedure>' returns a type an endpoint cannot use (e.g. list[dict[Str, Int]])",
        "return a column value, a record or Row[T], or a list of records or rows",
    ),
    # ---- pycheck codes (Q+) ----
    "PY800": ("the app's code does not parse", "return complete, syntactically valid files"),
    "PY801": (
        "a route reads, changes or deletes rows of an owned table -- owned directly, through "
        "a parent, or linked to owned rows -- without checking that they are the caller's",
        "filter by the owner column: `.where(<Table>.<owner column> == <caller>.id)`; for "
        "rows owned through a parent or linked to owned rows, join the parent and filter by "
        "its owner column",
    ),
    "PY802": (
        "a private column reaches a non-admin response",
        "leave it out of the response model and of any returned dict (only admin-only "
        "routes may return it)",
    ),
    "PY803": (
        "a request string or number is written to the database, or a page size is used as "
        "a row limit, without a bound",
        "add `max_length` to strings and `ge`/`le` to numbers on the request model or "
        "parameter; a page size gets `le=` at most 100",
    ),
    "PY804": (
        "a literal path is declared after a parameter path of the same method that also "
        "matches it, which swallows it",
        "declare the literal route before the parameter route",
    ),
    "PY805": (
        "an HTTP error differs from the app's own answer for the same case (e.g. a "
        "non-admin refused with another status or detail than the app's admin check gives)",
        "reuse the app's dependency for the case (e.g. `dependencies=[Depends(<admin "
        "check>)]` on the route), or raise exactly its status and detail",
    ),
    "PY806": (
        "a request datetime, which may carry any UTC offset, is compared with a stored "
        "datetime that the database returns as naive UTC",
        "convert it first: `if <datetime>.tzinfo is not None: <datetime> = "
        "<datetime>.astimezone(UTC).replace(tzinfo=None)`",
    ),
    "PY807": (
        "a commit (or a call to a helper that commits) comes before a check that can still "
        "refuse the request, so a refused request leaves its earlier writes in the database",
        "check everything that can refuse the request first, then write and call "
        "`<session>.commit()` once at the end",
    ),
}


def generic_row(code: str, count: int) -> MistakeRow:
    """``code``'s generic row (a code without one is a build error: write it first)."""
    if code not in GENERIC_ROWS:
        raise KeyError(f"{code} has no generic row in GENERIC_ROWS: write one")
    mistake, fix = GENERIC_ROWS[code]
    return MistakeRow(code, cap(mistake), cap(fix), count)


# ---- L+: the C3 persistence data -------------------------------------------------------------


def lattice_rows(pattern: str = L_PATTERN) -> list[MistakeRow]:
    from bench.analysis.diag_persistence import persistence, runs, transitions
    from lattice.check import check_source
    from lattice.diagnostics import Severity

    all_runs = runs(str(ROOT / pattern))
    table, _ = transitions(all_runs)
    today: set[str] = set()  # the codes today's checker reports on the refused rounds
    for key in sorted(all_runs):
        for r in all_runs[key]:
            if r.problem != "static":
                continue
            files = r.path / "files"
            module = types.ModuleType("_aids_profile")
            try:
                exec((files / "profile.py").read_text(), module.__dict__)  # noqa: S102
                profile = module.profile()
            except Exception:  # noqa: BLE001 -- a broken profile is not a checker refusal
                continue
            for program in sorted(files.glob("*.lattice.py")):
                for d in check_source(program.read_text(), profile).diagnostics:
                    if d.severity is Severity.ERROR:
                        today.add(d.code)

    def rank(code: str) -> tuple[Any, ...]:
        c = table[code]
        return (-c["repeated"], -(persistence(c) or 0.0), -sum(c.values()), code)

    rows = []
    for code in sorted(table, key=rank):
        if table[code]["repeated"] == 0 or code not in today:
            continue  # never persisted, or today's checker no longer reports it
        rows.append(generic_row(code, table[code]["repeated"]))
    return rows


# ---- Q+: the final round of every round-4 arm-P run, checked by pycheck ---------------------


def _final_rounds(pattern: str) -> Iterable[tuple[str, Path]]:
    """(ticket, the final round's ``files/`` directory) of every arm-P run whose results
    file matches ``pattern`` -- from the saved artefacts alone (never the results
    JSONL: its records carry the oracle's verdicts)."""
    for results in sorted(glob.glob(str(ROOT / pattern))):
        root = Path(results + ".artefacts")
        for rep in sorted(root.glob("*/P/*/rep*")):
            rounds = sorted(rep.glob("round*"), key=lambda p: int(p.name[5:]))
            with_files = [d / "files" for d in rounds if (d / "files").is_dir()]
            if with_files:
                yield rep.parts[-2], with_files[-1]


def python_rows(pattern: str = P_PATTERN) -> tuple[list[MistakeRow], dict[str, int]]:
    from bench.edit_slice.qcheck import check_files
    from bench.edit_slice.tickets import ticket

    flagged: Counter[str] = Counter()
    finals = with_findings = 0
    for tid, files_dir in _final_rounds(pattern):
        finals += 1
        files = {
            p.relative_to(files_dir).as_posix(): p.read_text()
            for p in sorted(files_dir.rglob("*.py"))
        }
        codes = {f.code for f in check_files(ticket(tid).repo, files)}
        with_findings += bool(codes)
        flagged.update(codes)
    ranked = sorted(flagged, key=lambda c: (-flagged[c], c))
    rows = [generic_row(code, flagged[code]) for code in ranked]
    return rows, {"final_rounds": finals, "final_rounds_flagged": with_findings}


# ---- build -----------------------------------------------------------------------------------


def fit(rows: list[MistakeRow], budget: int) -> list[MistakeRow]:
    """The longest prefix of ``rows`` whose rendered table is at most ``budget`` chars."""
    kept: list[MistakeRow] = []
    for row in rows:
        if len(render_table([*kept, row])) > budget:
            break
        kept.append(row)
    return kept


def build() -> dict[str, dict[str, Any]]:
    """{lang: the table file's content}."""
    l_rows = lattice_rows()[:MAX_ROWS]
    q_rows, q_stats = python_rows()
    q_rows = q_rows[:MAX_ROWS]
    budget = min(len(render_table(l_rows)), len(render_table(q_rows)))
    l_kept, q_kept = fit(l_rows, budget), fit(q_rows, budget)
    common = {"chars_budget": budget, "cell_chars": CELL_CHARS, "max_rows": MAX_ROWS}
    return {
        "lattice": {
            "source": L_PATTERN,
            "method": "C3 repair persistence of arm-L checker refusals; codes ranked by "
            "repeated refusals, then persistence, then refused rounds; row text: the "
            "code's generic, ticket-neutral row",
            "candidates": len(l_rows),
            **common,
            "chars": len(render_table(l_kept)),
            "rows": [vars(r) for r in l_kept],
        },
        "python": {
            "source": P_PATTERN,
            "method": "the final candidate of every round-4 arm-P run (whatever the oracle "
            "said) checked by pycheck with arm Q's policy; codes ranked by runs flagged; row "
            "text: the code's generic, ticket-neutral row",
            "candidates": len(q_rows),
            **q_stats,
            **common,
            "chars": len(render_table(q_kept)),
            "rows": [vars(r) for r in q_kept],
        },
    }


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bench.edit_slice.build_aids")
    ap.add_argument("--check", action="store_true", help="compare with the committed files")
    args = ap.parse_args(argv)
    tables = build()
    stale = []
    for lang, data in tables.items():
        path = TABLE_FILES[lang]  # type: ignore[index]
        text = _dump(data)
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(str(path))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f"{path.relative_to(ROOT)}: {len(data['rows'])} rows", file=sys.stderr)
    if stale:
        print("stale: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
