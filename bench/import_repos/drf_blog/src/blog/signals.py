# ruff: noqa
from django.db.models.signals import pre_save
from django.utils.text import slugify
from django.dispatch import receiver

from bench.import_repos.drf_blog.src.extensions.utils import slug_generator  # LATTICE MOD (was `from extensions.utils import slug_generator`: import path)

from .models import Blog


@receiver(pre_save, sender=Blog)
def save_slug_blog(sender, instance, *args, **kwargs):
    if len(instance.slug) <= 5:
        instance.slug = slugify(slug_generator(10))
