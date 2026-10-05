# ruff: noqa
from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bench.import_repos.conduit.api'  # LATTICE MOD (was `name = 'api'`: package path under bench/; the label stays the last component)
