"""Fixtures for the security-module tests: the in-repo minishop app (clean and holed per
class) wired as a :class:`~lattice.perf.measure.Harness`, and as a throwaway Studio project
so the CLI can be exercised end to end."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from lattice.perf.measure import Harness
from lattice.security.target import start_target_harness
from tests.spec import minishop


@pytest.fixture(autouse=True)
def _isolate_import_state():
    """Running the CLI in-process loads the throwaway project's ``spec:SPEC`` and inserts its
    directory on ``sys.path`` (as ``lattice check`` does). Undo both after each test so a
    ``spec`` module or a temp path never leaks into another test module (e.g. tests/studio)."""
    mods = set(sys.modules)
    path = list(sys.path)
    cwd = os.getcwd()
    try:
        yield
    finally:
        for name in set(sys.modules) - mods:
            if name == "spec" or name.startswith(("spec.", "minishop_sec_")):
                sys.modules.pop(name, None)
        sys.path[:] = path
        try:
            os.chdir(cwd)
        except OSError:
            pass


@pytest.fixture
def attack_spec():
    return minishop.attack_spec()


@pytest.fixture
def make_target(tmp_path: Path) -> Callable[[str], Callable]:
    """``make_target(variant)`` -> a factory that yields a fresh started Target of the
    minishop app holed for ``variant`` (``"ok"`` is the clean app)."""

    def factory_for(variant: str) -> Callable:
        _pkg, package = minishop.write_sec_app(tmp_path, variant)

        def factory():
            return start_target_harness(
                Harness(
                    app_factory=minishop.app_factory(package),
                    db_factory=minishop.build_db,
                    runtime=None,
                    login=minishop.login,
                    database="sqlite",
                )
            )

        return factory

    return factory_for


@pytest.fixture
def make_project(tmp_path: Path) -> Callable[[str], Path]:
    """``make_project(variant)`` -> a project directory with a studio.toml and spec.py that
    hook up the minishop app holed for ``variant``, so ``lattice attack --project DIR`` runs."""

    def build(variant: str) -> Path:
        _pkg, package = minishop.write_sec_app(tmp_path, variant)
        proj = tmp_path / f"proj_{variant}"
        proj.mkdir()
        (proj / "spec.py").write_text(
            "from tests.spec import minishop\nSPEC = minishop.attack_spec()\n"
        )
        (proj / "studio.toml").write_text(
            '[project]\nname = "minishop-sec"\n\n[app]\n'
            f'app = "{package}.main:make_app"\n'
            'db = "tests.spec.minishop:build_db"\n'
            'login = "tests.spec.minishop:login"\n'
        )
        return proj

    return build
