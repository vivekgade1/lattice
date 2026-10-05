"""A toy Django + DRF project for the importer's post-hoc soundness tests (2026-10-05).

Not an upstream app: written for the tests, with the shapes the held-out run showed the
front end mishandled, none of a held-out app's code:

* views that are plain ``ViewSet`` / ``APIView`` methods (no ``queryset``, no
  ``serializer_class``) reading and writing through ``Model.objects``,
  ``get_object_or_404``, a model serializer's ``save()`` and related managers
  (``topic.tags.add``), one through a module helper;
* an owner-or-staff object permission added per action in ``get_permissions``;
* the custom users model (``AUTH_USER_MODEL``) in an app listed after the app whose
  tables reference it;
* simplejwt's token obtain and refresh routes.

The tests copy this directory to a temporary root and import it as ``drf_toy``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bench.import_repos import _django_runtime

SETTINGS_MODULE = "drf_toy.settings"


def make_app(db_path: str | Path) -> Any:
    return _django_runtime.make_app(db_path, SETTINGS_MODULE)


def dispose(app: Any = None) -> None:
    _django_runtime.dispose()
