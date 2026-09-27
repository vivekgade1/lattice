"""sort(desc=[...]): one direction per `by` column (mixed-direction tie-breaks)."""

from pathlib import Path

import polars as pl
import pytest

from lattice.check import check_source
from lattice.diagnostics import Severity
from lattice.profile import Profile
from lattice.runtime.exec import run_source
from lattice.typesys import Scalar, Schema

ITEMS = Schema.from_pairs([("id", Scalar.INT), ("name", Scalar.STR), ("score", Scalar.FLOAT)])
PROFILE = Profile(schemas={"Items": ITEMS})
CSV = "id,name,score\n1,b,5.0\n2,a,5.0\n3,c,9.0\n4,a,1.0\n"


def _program(sort_line: str) -> str:
    return (
        "# lattice: v1\n"
        't = load("items.csv", schema=Items)\n'
        f"s = {sort_line}\n"
        'write(s, "out.parquet")\n'
    )


def _errors(src: str) -> list[str]:
    return [d.code for d in check_source(src, PROFILE).diagnostics if d.severity is Severity.ERROR]


def _run(src: str, tmp_path: Path) -> pl.DataFrame:
    (tmp_path / "items.csv").write_text(CSV)
    result, run = run_source(src, PROFILE, tmp_path)
    assert result.ok, result.diagnostics
    assert run is not None
    return pl.read_parquet(tmp_path / "out.parquet")


def test_mixed_directions_sort_per_column(tmp_path: Path) -> None:
    out = _run(_program('sort(t, by=["score", "id"], desc=[True, False])'), tmp_path)
    assert out["id"].to_list() == [3, 1, 2, 4]


def test_mixed_directions_on_text_tie_break(tmp_path: Path) -> None:
    out = _run(_program('sort(t, by=["score", "name"], desc=[True, False])'), tmp_path)
    assert out["name"].to_list() == ["c", "a", "b", "a"]


def test_single_bool_still_applies_to_every_column(tmp_path: Path) -> None:
    out = _run(_program('sort(t, by=["score", "id"], desc=True)'), tmp_path)
    assert out["id"].to_list() == [3, 2, 1, 4]


@pytest.mark.parametrize(
    "sort_line",
    [
        'sort(t, by=["score", "id"], desc=[True])',  # wrong length
        'sort(t, by=["score", "id"], desc=[True, "no"])',  # non-bool element
        'sort(t, by=["score", "id"], desc=[True, 1])',  # int is not a bool
        'sort(t, by=["score"], desc="yes")',
    ],
)
def test_invalid_desc_is_one_L404(sort_line: str) -> None:
    assert _errors(_program(sort_line)) == ["L404"]


def test_prompt_documents_per_column_directions() -> None:
    from lattice.llm.prompt import lattice_system_prompt

    assert "desc=[True, False]" in lattice_system_prompt()
