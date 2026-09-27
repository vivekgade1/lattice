"""Output confinement: L506 (static) and R104 (runtime defense in depth)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lattice.check import check_source
from lattice.diagnostics import Span
from lattice.ir import Node, Plan
from lattice.profile import Profile
from lattice.runtime import LatticeRuntimeError
from lattice.runtime.exec import execute, run_source
from lattice.typesys import Scalar, Schema

ITEMS = Schema.from_pairs([("id", Scalar.INT), ("name", Scalar.STR)])
CONFINED = Profile(schemas={"Items": ITEMS}, outputs=frozenset({"out.parquet"}))
OPEN = Profile(schemas={"Items": ITEMS})


def src(path: str) -> str:
    return f'# lattice: v1\nt = load("items.csv", schema=Items)\nwrite(t, "{path}")\n'


def codes(path: str, profile: Profile = CONFINED) -> list[str]:
    return [d.code for d in check_source(src(path), profile).diagnostics]


def test_profile_outputs_default_is_unrestricted() -> None:
    assert OPEN.outputs is None


@pytest.mark.parametrize("path", ["out.parquet", "./out.parquet"])
def test_allowed_path_ok(path: str) -> None:
    assert codes(path) == []


@pytest.mark.parametrize(
    "path", ["leak.csv", "OUT.parquet", "out.csv", "sub/out.parquet", ".//out.parquet"]
)
def test_other_paths_are_l506(path: str) -> None:
    # Review focus 3: spellings other than a leading "./" are not the declared output
    diags = check_source(src(path), CONFINED).diagnostics
    assert [d.code for d in diags] == ["L506"]
    assert diags[0].message == "this task may only write: out.parquet"
    assert diags[0].span.line == 3


def test_several_outputs_listed_sorted() -> None:
    profile = Profile(schemas={"Items": ITEMS}, outputs=frozenset({"b.csv", "a.parquet"}))
    diags = check_source(src("c.csv"), profile).diagnostics
    assert diags[0].message == "this task may only write: a.parquet, b.csv"


def test_bad_path_reports_its_own_error_only() -> None:
    assert codes("/etc/out.parquet") == ["L501"]
    assert codes("leak.txt") == ["L505"]


@pytest.mark.parametrize("path", ["leak.csv", "OUT.parquet", "x/y.parquet"])
def test_unrestricted_profile_allows_any_write(path: str) -> None:
    assert codes(path, OPEN) == []


def test_run_source_confined_write(tmp_path: Path) -> None:
    (tmp_path / "items.csv").write_text("id,name\n1,a\n")
    result, run = run_source(src("./out.parquet"), CONFINED, tmp_path)
    assert result.ok and run is not None
    assert (tmp_path / "out.parquet").exists()


def _plan(path: str) -> Plan:
    load = Node("t", "load", (), {"path": "items.csv"}, ITEMS, Span(2, 5))
    write = Node("_write0", "write", ("t",), {"path": path}, None, Span(3, 1))
    return Plan((load, write))


def test_runtime_r104_on_write_outside_outputs(tmp_path: Path) -> None:
    (tmp_path / "items.csv").write_text("id,name\n1,a\n")
    with pytest.raises(LatticeRuntimeError) as exc:
        execute(_plan("leak.csv"), tmp_path, outputs=frozenset({"out.parquet"}))
    assert exc.value.code == "R104"
    assert exc.value.span == Span(3, 1)
    assert not (tmp_path / "leak.csv").exists()


def test_runtime_allows_declared_output_and_unrestricted(tmp_path: Path) -> None:
    (tmp_path / "items.csv").write_text("id,name\n1,a\n")
    out = execute(_plan("./out.parquet"), tmp_path, outputs=frozenset({"out.parquet"}))
    assert "./out.parquet" in out.outputs
    assert execute(_plan("leak.csv"), tmp_path).outputs["leak.csv"].height == 1
