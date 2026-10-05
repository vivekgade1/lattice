# ruff: noqa
from django.urls import path, include

app_name = "blog"


urlpatterns = [
    path("api/", include("bench.import_repos.drf_blog.src.blog.api.urls", namespace="blog-api"))  # LATTICE MOD (was `path("api/", include("blog.api.urls", namespace="blog-api"))`: import path)
]   
