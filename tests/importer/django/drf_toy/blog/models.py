from django.conf import settings
from django.db import models


class Tag(models.Model):
    name = models.CharField(max_length=30, unique=True)


class Topic(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="topics",
                             on_delete=models.CASCADE)
    title = models.CharField(max_length=100)
    tags = models.ManyToManyField(Tag, blank=True)


class Comment(models.Model):
    topic = models.ForeignKey(Topic, related_name="comments", on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    content = models.CharField(max_length=200)
