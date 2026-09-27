from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from bench.api_datasets import api_profile, build_db
from lattice.check import CheckedProgram, check_source


def source(src: str) -> str:
    return "# lattice: v1\n" + dedent(src).lstrip("\n")


def checked(src: str) -> CheckedProgram:
    result = check_source(source(src), api_profile())
    assert result.program is not None, [d.render() for d in result.diagnostics]
    return result.program


@pytest.fixture(scope="session")
def pristine_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("apidb") / "pristine.db"
    build_db(path)
    return path


@pytest.fixture
def db(pristine_db: Path, tmp_path: Path) -> Path:
    path = tmp_path / "shop.db"
    path.write_bytes(pristine_db.read_bytes())
    return path
