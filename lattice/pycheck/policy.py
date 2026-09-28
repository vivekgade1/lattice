"""An app's policy: the guarantees pycheck checks the edited code against.

A :class:`Policy` is derived once per app from the *original* code
(:func:`lattice.pycheck.derive.derive_policy`, through the importer's owner and private
inference) and handed to :func:`lattice.pycheck.run_pycheck` with each edited copy. It
can also be written by hand. All names are the app's own: table classes (``Item``),
columns (``owner_id``), dependencies (``get_current_active_superuser``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AdminConcept:
    """The app's superuser concept: the caller's flag column, the dependency that
    requires it, and what that dependency answers a non-admin."""

    column: str
    dependency: str
    status: int | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ErrorAnswer:
    """How the app answers a case: status and (when the app uses one) the detail."""

    status: int
    detail: str | None = None


@dataclass(frozen=True)
class Policy:
    package: str  # the app package, dotted (``bench.template_app.app``)
    users_table: str | None = None  # the table class the current-user dependency returns
    caller: str = "current_user"  # how the app's routes name the signed-in caller
    # table class -> its owner column (a foreign key to the users table)
    owners: Mapping[str, str] = field(default_factory=dict)
    # table class -> (foreign key, owned parent table class): owned through the parent
    owner_via: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    # table class -> ((foreign key, owned parent), ...): a link table between owned rows
    links: Mapping[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)
    # table class -> the columns that never reach a non-admin response
    private: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    admin: AdminConcept | None = None
    # table class -> the answer to another user's row (403, or 404 for owner-scoped
    # lookups)
    not_owned: Mapping[str, ErrorAnswer] = field(default_factory=dict)
    # table class -> a route of the original that scopes its rows to the caller
    examples: Mapping[str, str] = field(default_factory=dict)
    # stored datetimes read back naive (SQLite)
    naive_datetimes: bool = True
    page_limit_max: int = 100
    # the keys of the original app's own findings: its accepted conventions
    baseline: frozenset[str] = frozenset()


__all__ = ["AdminConcept", "ErrorAnswer", "Policy"]
