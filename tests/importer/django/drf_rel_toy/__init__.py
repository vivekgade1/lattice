"""A toy Django + DRF project for the relationship-write tests (pivot plan 2026-10-05,
A.2). Not an upstream app: written for the tests.

Link tables written only through relationships, never named by a route:

* ``Post.tags``' table, through a local holding the manager (``tags = post.tags;
  tags.add(...)``);
* ``Star`` (``Post.starrers``' explicit ``through`` model), by a model method the view
  calls (``post.star(user)`` running ``self.starrers.add(user)``);
* ``Post.categories``' table, by a model serializer writing that many-to-many field;
* ``Post.likers``' table, by ``post.likers.add(request.user)`` in the view.

``Archive`` is touched by no route: the one table truly outside the API. With
``REL_TOY_UNRESOLVED=1`` the project also routes ``POST /api/posts/<pk>/attach/<field>/``,
which adds through ``getattr(post, field)`` -- a relationship the front end cannot name.

The tests copy this directory to a temporary root and import it as ``drf_rel_toy``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bench.import_repos import _django_runtime

SETTINGS_MODULE = "drf_rel_toy.settings"


def make_app(db_path: str | Path) -> Any:
    return _django_runtime.make_app(db_path, SETTINGS_MODULE)


def dispose(app: Any = None) -> None:
    _django_runtime.dispose()
