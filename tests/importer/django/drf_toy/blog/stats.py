class Stats:
    """Package code a view calls through a class: the front end does not follow it."""

    @staticmethod
    def count():
        from drf_toy.blog.models import Topic

        return Topic.objects.count()
