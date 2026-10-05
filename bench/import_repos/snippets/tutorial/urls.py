# ruff: noqa
# Tutorial part 1 (root urlconf) + part 4 (browsable-API login views).
from django.urls import path, include

urlpatterns = [
    path("", include("bench.import_repos.snippets.snippets.urls")),  # LATTICE MOD (was "snippets.urls": import path)
]

urlpatterns += [
    path("api-auth/", include("rest_framework.urls")),
]
