"""A small FastAPI + SQLModel app (package ``demo``) for the pycheck tests.

Its shape follows the template: ``Item`` rows are owned through ``owner_id``, superusers
bypass ownership, another user's item is a 403 "Not enough permissions", the admin
dependency answers 403 "The user doesn't have enough privileges", ``hashed_password``
is never returned, and SQLite reads datetimes back naive. :func:`demo_app` writes it
(optionally with ``routes.py`` extended or files replaced) under a temporary directory
and returns the package directory.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from pycheck_demo import FILES


def write_app(root: Path, files: Mapping[str, str]) -> Path:
    """The ``demo`` package under ``root`` with ``files`` (relative path -> source)."""
    pkg = root / "demo"
    for rel, src in files.items():
        path = pkg / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src)
    return pkg


@pytest.fixture
def demo_app(tmp_path: Path) -> Callable[..., Path]:
    """``demo_app(extra_routes="...", **replaced)``: the demo app with ``extra_routes``
    appended to ``routes.py`` and any file replaced (``models="..."``), in its own
    directory per call."""
    count = 0

    def make(extra_routes: str = "", **replaced: str) -> Path:
        nonlocal count
        count += 1
        files = dict(FILES)
        for name, src in replaced.items():
            files[f"{name}.py"] = src
        if extra_routes:
            files["routes.py"] = files["routes.py"] + "\n\n" + extra_routes
        return write_app(tmp_path / f"app{count}", files)

    return make
