"""Round 4, D5: the tables of a gRPC app come from the Go source when it declares any, else from the
database the ``studio.toml`` ``db`` harness builds (``tables_source: harness_db``). The database is built in a
fresh temporary directory outside the target and removed after; a harness that fails is
``unread: harness_db_failed``, never an exception out of the model."""

from __future__ import annotations

# ruff: noqa: E501 -- docstrings and data lines
import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

from tests.grpc import oracle
from tests.grpc.conftest import needs_go

T4 = oracle.FIXTURES / "tables4"
TRUTH_SHA = "bd06dc85c150c46a91c8b27f2584b3e0872cf1086b6af90dc49e1dcfa39cf956"

pytestmark = needs_go


@pytest.fixture(scope="module")
def truth() -> Any:
    return oracle.load(T4 / "truth.json")["cases"]


def ex(front_end: Any, case: str) -> dict[str, Any]:
    return front_end.extract_json(T4 / case, ".")  # type: ignore[no-any-return]


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_the_truth_was_hashed_before_the_library_changed() -> None:
    assert hashlib.sha256((T4 / "truth.json").read_bytes()).hexdigest() == TRUTH_SHA


def test_ddl_in_go_is_the_round_3_path_and_runs_no_harness(front_end: Any, truth: Any) -> None:
    d = ex(front_end, "gormtables")
    assert sorted(t["table"] for t in d["tables"]) == truth["gormtables"]["tables"]
    assert "tables_source" not in d


def test_ddl_only_in_the_harness_is_read_from_its_database(front_end: Any, truth: Any) -> None:
    d = ex(front_end, "harnessdb")
    assert sorted(t["table"] for t in d["tables"]) == truth["harnessdb"]["tables"]
    assert d["tables_source"] == "harness_db"
    orders = next(t for t in d["tables"] if t["table"] == "orders")
    assert [c["name"] for c in orders["columns"]] == truth["harnessdb"]["columns"]["orders"]


def test_the_columns_carry_keys_types_and_foreign_keys(front_end: Any) -> None:
    d = ex(front_end, "harnessdb")
    cols = {t["table"]: {c["name"]: c for c in t["columns"]} for t in d["tables"]}
    assert cols["accounts"]["id"]["primary_key"] is True and cols["accounts"]["id"]["type"] == "Int"
    assert cols["accounts"]["name"]["unique"] is True and cols["accounts"]["name"]["type"] == "Str"
    assert (
        cols["accounts"]["balance"]["type"] == "Float" and cols["orders"]["paid"]["type"] == "Bool"
    )
    assert cols["sessions"]["account_id"]["foreign_key"] == "accounts.id"
    assert (
        cols["orders"]["total"]["required"] is True and cols["orders"]["paid"]["nullable"] is True
    )


def test_the_model_carries_the_harness_tables(front_end: Any) -> None:
    model = front_end.extract(T4 / "harnessdb", ".")
    assert sorted(t.table_name for t in model.tables) == ["accounts", "orders", "sessions"]


def test_the_temporary_database_is_removed_and_nothing_is_written_under_the_root(
    front_end: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "t" / "harnessdb"
    shutil.copytree(T4 / "harnessdb", root)
    before = snapshot(root)
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    d = front_end.extract_json(root, ".")
    assert d["tables_source"] == "harness_db"
    assert list(scratch.iterdir()) == []  # the fresh directory was removed
    assert snapshot(root) == before  # no database, no bytecode, nothing under the target


def test_a_harness_that_cannot_build_is_unread_not_an_exception(front_end: Any, truth: Any) -> None:
    d = ex(front_end, "brokendb")
    assert d["tables"] == [] and "tables_source" not in d
    (u,) = [x for x in d["unread"] if x["what"] == "harness_db_failed"]
    assert "seed data is missing" in u["reason"]
    assert front_end.extract(T4 / "brokendb", ".").tables == ()


def test_go_tables_win_the_harness_is_not_run(front_end: Any, tmp_path: Path) -> None:
    root = tmp_path / "g"
    shutil.copytree(T4 / "gormtables", root)
    mark = tmp_path / "ran"
    (root / "studio.toml").write_text('[app]\napp = "harness:make_app"\ndb = "harness:build_db"\n')
    (root / "harness.py").write_text(
        f"from pathlib import Path\n\n\ndef build_db(path):\n    Path({str(mark)!r}).write_text('x')\n"
    )
    d = front_end.extract_json(root, ".")
    assert [t["table"] for t in d["tables"]] == ["notes"] and "tables_source" not in d
    assert not mark.exists()


def test_a_tree_without_a_harness_has_no_tables_and_no_source(
    front_end: Any, tmp_path: Path
) -> None:
    root = tmp_path / "n"
    shutil.copytree(T4 / "harnessdb", root)
    (root / "studio.toml").unlink()
    d = front_end.extract_json(root, ".")
    assert d["tables"] == [] and "tables_source" not in d
    assert not [u for u in d["unread"] if u["what"] == "harness_db_failed"]


def test_the_harness_is_looked_for_in_the_module_directory_not_the_extract_root(
    front_end: Any,
) -> None:
    """The app layout the importer and the runner use: ``extract_json(parent, "<app>")`` with
    ``studio.toml`` inside ``<app>`` (found by PD4-HARBOR, whose tables_source was ``go`` before)."""
    d = front_end.extract_json(T4, "harnessdb")
    assert d["tables_source"] == "harness_db" and len(d["tables"]) == 3
