"""Arm Q's checker on the edit slice: pycheck with each app's policy.

The policy of an app (:mod:`bench.edit_slice.apps`) is derived once, from the original
app (:func:`lattice.pycheck.derive_policy`); a candidate (an overlay ``{path relative to
the app package: source}``, as :mod:`bench.edit_slice.variant` loads it) is written to a
temporary directory beside the original's other files and checked against it
(:func:`check_files`). The checks see only the edited code, never the hidden oracle.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from functools import cache
from pathlib import Path

from bench.edit_slice.apps import repo
from lattice.pycheck import Finding, Policy, derive_policy, materialize, run_pycheck


@cache
def repo_policy(repo_name: str) -> Policy:
    """The policy of the app ``repo_name``, from its original code (once per process)."""
    app = repo(repo_name)
    return derive_policy(app.root, app.package)


def check_files(repo_name: str, files: Mapping[str, str], *, strict: bool = False) -> list[Finding]:
    """pycheck's findings on the app ``repo_name`` with ``files`` replaced."""
    app = repo(repo_name)
    with tempfile.TemporaryDirectory(prefix="pycheck-") as tmp:
        app_dir = materialize(app.root, app.package, files, Path(tmp))
        return run_pycheck(app_dir, repo_policy(repo_name), strict=strict)


__all__ = ["check_files", "repo_policy"]
