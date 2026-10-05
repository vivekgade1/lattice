# ruff: noqa
from django.apps import AppConfig


class BlogConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bench.import_repos.drf_blog.src.blog'  # LATTICE MOD (was `name = 'blog'`: package path under bench/; the label stays the last component)

    def ready(self):
        import bench.import_repos.drf_blog.src.blog.signals  # LATTICE MOD (was `import blog.signals`: import path)