# ruff: noqa
from django.apps import AppConfig


class CommentConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bench.import_repos.drf_blog.src.comment'  # LATTICE MOD (was `name = 'comment'`: package path under bench/; the label stays the last component)
    verbose_name = "Comments"