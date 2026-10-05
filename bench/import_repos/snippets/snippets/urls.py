# ruff: noqa
# Tutorial part 6 (router): the tutorial's final state.
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from bench.import_repos.snippets.snippets import views  # LATTICE MOD (was `from snippets import views`: import path)

# Create a router and register our ViewSets with it.
router = DefaultRouter()
router.register(r"snippets", views.SnippetViewSet, basename="snippet")
router.register(r"users", views.UserViewSet, basename="user")

# The API URLs are now determined automatically by the router.
urlpatterns = [
    path("", include(router.urls)),
]
