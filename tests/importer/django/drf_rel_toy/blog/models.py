from django.conf import settings
from django.db import models


class Tag(models.Model):
    name = models.CharField(max_length=30, unique=True)


class Category(models.Model):
    name = models.CharField(max_length=30, unique=True)


class Post(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="posts",
                             on_delete=models.CASCADE)
    title = models.CharField(max_length=100)
    tags = models.ManyToManyField(Tag, blank=True)
    categories = models.ManyToManyField(Category, blank=True)
    likers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True,
                                    related_name="liked_posts")
    starrers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True,
                                      through="Star", related_name="starred_posts")

    def star(self, user):
        self.starrers.add(user)


class Star(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    post = models.ForeignKey(Post, on_delete=models.CASCADE)


class Archive(models.Model):
    note = models.CharField(max_length=100)
