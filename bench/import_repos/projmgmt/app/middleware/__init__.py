# ruff: noqa
"""Middleware package."""

from bench.import_repos.projmgmt.app.middleware.auth import get_current_user_id, manager_required  # LATTICE MOD (was `from app.middleware.auth import get_current_user_id, manager_required`: import path)

__all__ = ['get_current_user_id', 'manager_required']
