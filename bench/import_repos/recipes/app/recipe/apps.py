# ruff: noqa
from django.apps import AppConfig


class RecipeConfig(AppConfig):
    name = 'bench.import_repos.recipes.app.recipe'  # LATTICE MOD (was `name = 'recipe'`: package path under bench/; the label stays the last component)
