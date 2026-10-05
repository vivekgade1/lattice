from django.conf import settings
from django.db import models


class Tag(models.Model):
    name = models.CharField(max_length=30, unique=True)


class Topic(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="topics",
                             on_delete=models.CASCADE)
    title = models.CharField(max_length=100)
    tags = models.ManyToManyField(Tag, blank=True)


class Page(models.Model):
    """A shared table of formatted fields: a slug its routes look rows up by, a URL."""

    slug = models.SlugField(max_length=40, unique=True)
    link = models.URLField(max_length=200)
    title = models.CharField(max_length=100)


class Comment(models.Model):
    topic = models.ForeignKey(Topic, related_name="comments", on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    content = models.CharField(max_length=200)
