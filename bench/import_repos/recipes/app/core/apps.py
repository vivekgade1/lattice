# ruff: noqa
from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'bench.import_repos.recipes.app.core'  # LATTICE MOD (was `name = 'core'`: package path under bench/; the label stays the last component)
