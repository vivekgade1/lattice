# ruff: noqa
from django.apps import AppConfig


class UserConfig(AppConfig):
    name = 'bench.import_repos.recipes.app.user'  # LATTICE MOD (was `name = 'user'`: package path under bench/; the label stays the last component)
