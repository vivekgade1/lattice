# ruff: noqa
# Tutorial part 5 (hyperlinked serializers): the tutorial's final state.
from django.contrib.auth.models import User
from rest_framework import serializers
from bench.import_repos.snippets.snippets.models import Snippet  # LATTICE MOD (was `from snippets.models`: import path)


class SnippetSerializer(serializers.HyperlinkedModelSerializer):
    owner = serializers.ReadOnlyField(source="owner.username")
    highlight = serializers.HyperlinkedIdentityField(
        view_name="snippet-highlight", format="html"
    )

    class Meta:
        model = Snippet
        fields = [
            "url",
            "id",
            "highlight",
            "owner",
            "title",
            "code",
            "linenos",
            "language",
            "style",
        ]


class UserSerializer(serializers.HyperlinkedModelSerializer):
    snippets = serializers.HyperlinkedRelatedField(
        many=True, view_name="snippet-detail", read_only=True
    )

    class Meta:
        model = User
        fields = ["url", "id", "username", "snippets"]
