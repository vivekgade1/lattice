"""The L+ and Q+ mistakes tables are ticket-neutral and oracle-blind (round-5 review C1).

* **Neutral rows.** Every row is its code's generic row (``build_aids.GENERIC_ROWS``):
  no route path, function name, table or column name, trap string or oracle case name of
  any ticket or app appears in either table -- the tables are scanned against
  ``tickets.py``, the hidden oracle's ticket-specific cases, the oracle tests' traps,
  the Python and Lattice references (and what they add to the original app) and the
  three apps' own tables, columns and routes.
* **Oracle-blind evidence.** Q+'s codes are ranked by how often pycheck fired on the
  final round of *every* round-4 arm-P run, whatever the oracle said: the build reads
  only the saved artefacts, never the results JSONL (which holds the oracle's verdicts).
  L+'s codes come from checker refusals (the rounds' own feedback) only.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import re
from functools import cache
from pathlib import Path

import pytest

from bench.analysis import diag_persistence
from bench.edit_slice import aids, build_aids, oracle, run
from bench.edit_slice.apps import REPOS
from bench.edit_slice.lattice_reference import has_lattice_reference, lattice_reference_files
from bench.edit_slice.reference import reference_files
from bench.edit_slice.tickets import ROUND4_TICKETS
from lattice.pycheck.view import AppView

ROOT = Path(__file__).resolve().parents[2]
TRAP_MODULES = ("test_edit_slice_oracle", "test_edit_slice_repos", "test_edit_slice_round4")


def _table_text(lang: aids.Lang) -> str:
    return "\n".join(f"{r.mistake}\n{r.fix}" for r in aids.load_table(lang))


def _functions(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover
        return set()
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}


def _strings(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover
        return set()
    return {
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


@cache
def forbidden() -> dict[str, set[str]]:
    """kind -> the ticket- and app-specific strings no row may contain."""
    paths: set[str] = set()
    functions: set[str] = set()
    strings: set[str] = set()
    cases: set[str] = set()
    for t in ROUND4_TICKETS:
        for route in t.routes:
            path = route.split(" ", 1)[1]
            paths.add(path)
            paths.add(path.removeprefix("/api/v1"))
            for seg in path.strip("/").split("/"):
                if seg and not seg.startswith("{") and seg not in ("api", "v1"):
                    paths.add("/" + seg)
        for rel, src in reference_files(t.id).items():
            functions |= _functions(src)
            original = run.p_view({}, rel, t.repo) or ""
            strings |= _strings(src) - _strings(original)
        if has_lattice_reference(t.id):
            for name, src in lattice_reference_files(t.id).items():
                if name.endswith(".lattice.py"):
                    functions |= _functions(src)
        cases |= set(oracle.ticket_oracle(t.id).specific)
    for name in TRAP_MODULES:
        spec = importlib.util.spec_from_file_location(
            f"_aids_traps_{name}", ROOT / "tests" / "bench" / f"{name}.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cases |= set(module.TRAPS)
    tables: set[str] = set()
    columns: set[str] = set()
    for app in REPOS.values():
        model = AppView(app.root, app.package).model
        for table in model.tables:
            tables |= {table.name, table.table_name}
            columns |= {c.name for c in table.columns}
        functions |= {r.name for r in model.routes}
        functions |= {d.name for d in model.dependencies}
    # `user` is Lattice's own word for the caller (``by=user``, ``user: CurrentUser``)
    tables -= {"user", "users"}
    # a column name that is an ordinary English word (id, title, name, text) can't leak a
    # ticket; one with an underscore or a capital inside (owner_id, createdAt) can
    columns = {c for c in columns if "_" in c or c != c.lower()}
    strings = {s for s in strings if len(s.strip()) >= 4 and not s.strip().isdigit()}
    return {
        "path": paths,
        "function": {f for f in functions if len(f) >= 4},
        "string": strings,
        "case": cases,
        "table": tables,
        "column": columns,
    }


QUOTED_NAME = re.compile(r"""['"`]([A-Za-z_]\w*)['"`]""")


# the languages' own words a generic row may quote (Python keywords, pydantic's bounds,
# Lattice's OneOf("a", "b") example)
VOCABULARY = frozenset({"raise", "a", "b", "max_length", "ge", "le"})


def quoted_names(text: str) -> list[str]:
    """Bare identifiers in quotes (``'stripped_title'``, ``col("description")``) that are
    not the languages' own words: a name taken from someone's program. A generic row
    quotes only placeholders."""
    return [n for n in QUOTED_NAME.findall(text) if n not in VOCABULARY]


def _found(text: str, needle: str) -> bool:
    if re.fullmatch(r"\w+", needle):
        return re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", text) is not None
    return needle in text


@pytest.mark.parametrize("lang", ["lattice", "python"])
def test_no_row_names_a_ticket_or_app_identifier(lang):
    text = _table_text(lang)
    leaks = [
        (kind, needle)
        for kind, needles in forbidden().items()
        for needle in sorted(needles)
        if _found(text, needle)
    ]
    assert leaks == []
    assert quoted_names(text) == []


def test_the_scan_would_catch_the_leaks_the_review_found():
    """The pre-fix rows (t29's route and fix, t4's and t2's traps, the L+ row naming
    ``Note``) are all caught by the scan above."""
    old = [
        "GET /tags/summary (get_tags_summary) reads NoteTagLink rows, which link Note rows",
        "join Note and add `.where(Note.owner_id == current_user.id)` (as `get_notes` does)",
        'restore_item refuses a non-admin with 403 "Not enough permissions"',
        "the request datetime 'since' may carry any UTC offset, but Item.created_at is",
        "the profile declares none for Note, so its rows are shared",
        "'stripped_title' is a table, not a value",
    ]
    for line in old:
        hits = [n for ns in forbidden().values() for n in ns if _found(line, n)]
        assert hits or quoted_names(line), line


@pytest.mark.parametrize("lang", ["lattice", "python"])
def test_every_row_is_its_codes_generic_row(lang):
    for row in aids.load_table(lang):
        assert (row.mistake, row.fix) == build_aids.GENERIC_ROWS[row.code], row.code


def test_every_generic_row_is_neutral_too():
    """Not just the rows that made the cut: every generic row, so a rebuild on new
    evidence cannot pick a leaking one."""
    text = "\n".join(f"{m}\n{f}" for m, f in build_aids.GENERIC_ROWS.values())
    leaks = [(kind, n) for kind, ns in forbidden().items() for n in sorted(ns) if _found(text, n)]
    assert leaks == []
    assert quoted_names(text) == []


def test_the_tables_keep_the_equal_size_budget():
    lattice = json.loads(aids.TABLE_FILES["lattice"].read_text())
    python = json.loads(aids.TABLE_FILES["python"].read_text())
    assert lattice["chars_budget"] == python["chars_budget"]
    for data in (lattice, python):
        assert data["chars"] <= data["chars_budget"]


# ---- the evidence never includes the oracle's verdicts --------------------------------------


def _fake_p_results(tmp_path: Path, runs: dict[str, dict[str, str]]) -> str:
    """A round-4-style arm-P results file whose JSONL is not even JSON, and an artefact
    tree with one rep per ticket (its final round's files); returns the glob pattern."""
    jsonl = tmp_path / "round4_fake_P.jsonl"
    jsonl.write_text("this is not JSON: the build must not read the oracle's verdicts\n")
    root = Path(str(jsonl) + ".artefacts")
    for tid, files in runs.items():
        for k, content in ((1, {"broken.py": "x = ("}), (2, files)):
            d = root / "fake-model" / "P" / tid / "rep0" / f"round{k}" / "files"
            for rel, src in content.items():
                (d / rel).parent.mkdir(parents=True, exist_ok=True)
                (d / rel).write_text(src)
    return str(tmp_path / "round4_*_P.jsonl")


def test_q_plus_evidence_is_every_final_round_not_the_oracles_silent_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(build_aids, "ROOT", tmp_path)
    good = dict(reference_files("t2_since"))
    conversion = (
        "        if since.tzinfo is not None:\n"
        "            since = since.astimezone(UTC).replace(tzinfo=None)\n"
    )
    trap = {rel: src.replace(conversion, "") for rel, src in good.items()}
    assert trap != good
    runs = {"t2_since": trap, "t1_search": dict(reference_files("t1_search"))}
    pattern = _fake_p_results(tmp_path, runs)
    rows, stats = build_aids.python_rows(pattern)
    assert stats == {"final_rounds": 2, "final_rounds_flagged": 1}  # every run's final round
    assert [(r.code, r.count) for r in rows] == [("PY806", 1)]
    assert (rows[0].mistake, rows[0].fix) == build_aids.GENERIC_ROWS["PY806"]


def test_the_builders_do_not_read_the_oracle_outcomes():
    for fn in (build_aids.python_rows, build_aids.lattice_rows, diag_persistence.runs,
               diag_persistence.load_round, build_aids._final_rounds):  # fmt: skip
        source = inspect.getsource(fn)
        assert "outcome" not in source, fn.__name__
        assert "read_text()" not in source or ".jsonl" not in source, fn.__name__
    assert "json.loads(line)" not in inspect.getsource(build_aids)
