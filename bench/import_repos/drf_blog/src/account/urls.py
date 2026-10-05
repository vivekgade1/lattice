# ruff: noqa
from django.urls import path, include

app_name = "account"

urlpatterns = [
    path('api/', include("bench.import_repos.drf_blog.src.account.api.urls"))  # LATTICE MOD (was `path('api/', include("account.api.urls"))`: import path)
]   
