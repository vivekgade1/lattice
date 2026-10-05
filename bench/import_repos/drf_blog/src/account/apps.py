# ruff: noqa
from django.apps import AppConfig


class AccountConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "bench.import_repos.drf_blog.src.account"  # LATTICE MOD (was `name = "account"`: package path under bench/; the label stays the last component)
