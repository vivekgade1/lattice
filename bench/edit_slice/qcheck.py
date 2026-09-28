"""Arm Q's checker on the edit slice: pycheck with each app's policy.

The policy of an app (:mod:`bench.edit_slice.apps`) is derived from the original app
(:func:`lattice.pycheck.derive_policy`), with one exception: its **ownership facts**
(which tables have an owner column, which are owned through a parent, which are shared)
are the ones arm L's start profile declares (:func:`start_ownership`) -- the template's
``bench/template_profile.py``, a held-out repo's committed import
``bench/imports/<repo>/profile.py`` -- so arms Q and L are told, and checked for, the
same owners (round-5 review C2). The import is read when the policy is asked for, never
cached across a change to it; without an import, pycheck's own derivation
(:func:`derived_policy`) is the fallback.

A candidate (an overlay ``{path relative to the app package: source}``, as
:mod:`bench.edit_slice.variant` loads it) is written to a temporary directory beside the
original's other files and checked against the policy (:func:`check_files`). The checks
see only the edited code, never the hidden oracle.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from bench.edit_slice.apps import repo
from lattice.pycheck import Finding, Policy, derive_policy, materialize, run_pycheck

TEMPLATE_PROFILE = "bench/template_profile.py"


@dataclass(frozen=True)
class StartOwnership:
    """The ownership arm L's start profile declares. ``shared``: every other table but
    the users table (a table with neither an owner column nor ``owner_via``)."""

    owners: tuple[tuple[str, str], ...]
    owner_via: tuple[tuple[str, tuple[str, str]], ...]
    shared: tuple[str, ...]
    source: str  # the profile it was read from, relative to the repo root


def _ownership_of(profile: object, source: str) -> StartOwnership:
    api = profile.api  # type: ignore[attr-defined]
    tables = api.tables
    owners = tuple((t, s.owner) for t, s in tables.items() if s.owner)
    owner_via = tuple((t, tuple(s.owner_via)) for t, s in tables.items() if s.owner_via)
    owned = {t for t, _ in owners} | {t for t, _ in owner_via}
    shared = tuple(t for t in tables if t != api.users_table and t not in owned)
    return StartOwnership(owners, owner_via, shared, source)


def start_ownership(repo_name: str) -> StartOwnership | None:
    """The ownership arm L's start profile of ``repo_name`` declares, read now: the
    template's hand-written profile, or a held-out repo's committed import (None: no
    import yet). ``shared`` here lists every unowned table; the policy drops link tables
    from it (:func:`lattice.pycheck.derive.derive_policy`)."""
    if repo_name == "template":
        from bench.template_profile import template_profile

        return _ownership_of(template_profile(), TEMPLATE_PROFILE)
    from bench.edit_slice import run

    path = run.import_dir(repo_name) / "profile.py"
    if not path.is_file():
        return None
    profile = run.load_profile_source(path.read_text())
    source = path.relative_to(run.REPO_ROOT) if path.is_relative_to(run.REPO_ROOT) else path
    return _ownership_of(profile, source.as_posix())


@cache
def _policy(repo_name: str, owners: tuple, owner_via: tuple) -> Policy:
    app = repo(repo_name)
    return derive_policy(app.root, app.package, ownership=(dict(owners), dict(owner_via)))


@cache
def derived_policy(repo_name: str) -> Policy:
    """pycheck's own policy of ``repo_name``: every fact, ownership included, derived
    from the original code (the fallback, and pycheck's accuracy tests)."""
    app = repo(repo_name)
    return derive_policy(app.root, app.package)


def repo_policy(repo_name: str) -> Policy:
    """Arm Q's policy of ``repo_name``: pycheck's derivation, with the ownership facts of
    arm L's start profile (:func:`start_ownership`), read at call time."""
    start = start_ownership(repo_name)
    if start is None:
        return derived_policy(repo_name)
    return _policy(repo_name, start.owners, start.owner_via)


def check_files(
    repo_name: str,
    files: Mapping[str, str],
    *,
    strict: bool = False,
    policy: Policy | None = None,
) -> list[Finding]:
    """pycheck's findings on the app ``repo_name`` with ``files`` replaced (``policy``:
    default arm Q's, :func:`repo_policy`)."""
    app = repo(repo_name)
    with tempfile.TemporaryDirectory(prefix="pycheck-") as tmp:
        app_dir = materialize(app.root, app.package, files, Path(tmp))
        return run_pycheck(app_dir, policy or repo_policy(repo_name), strict=strict)


__all__ = [
    "TEMPLATE_PROFILE",
    "StartOwnership",
    "check_files",
    "derived_policy",
    "repo_policy",
    "start_ownership",
]
