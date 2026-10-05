# ruff: noqa
from django.apps import AppConfig


class SnippetsConfig(AppConfig):
    name = 'bench.import_repos.snippets.snippets'  # LATTICE MOD (was 'snippets': package path under bench/)
    label = 'snippets'  # LATTICE MOD (keeps the app label the upstream name 'snippets' gave)
