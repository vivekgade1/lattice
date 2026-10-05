# ruff: noqa
from django.urls import path, include

app_name = "comment"

urlpatterns = [
    path("api/", include("bench.import_repos.drf_blog.src.comment.api.urls", namespace="comment-api")),  # LATTICE MOD (was `path("api/", include("comment.api.urls", namespace="comment-api")),`: import path)
]
