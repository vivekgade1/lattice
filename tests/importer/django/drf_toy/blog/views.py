from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import (
    AllowAny,
    BasePermission,
    IsAuthenticated,
    IsAuthenticatedOrReadOnly,
)
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ReadOnlyModelViewSet, ViewSet

from drf_toy.blog.models import Comment, Tag, Topic
from drf_toy.blog.stats import Stats


class IsAdminOrOwner(BasePermission):
    def has_object_permission(self, request, view, obj):
        return request.user.is_staff or obj.user == request.user


class TopicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Topic
        fields = ["id", "user", "title"]
        read_only_fields = ["user"]


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name"]


class TagViewSet(ReadOnlyModelViewSet):
    """Rows looked up by a column that is not the key (``lookup_field``)."""

    queryset = Tag.objects.all()
    serializer_class = TagSerializer
    lookup_field = "name"


class TopicViewSet(ViewSet):
    permission_classes = (IsAuthenticatedOrReadOnly,)

    def get_permissions(self):
        self.permission_classes = (IsAuthenticatedOrReadOnly,)
        if self.action in ("update", "destroy"):
            self.permission_classes += (IsAdminOrOwner,)
        return super().get_permissions()

    def list(self, request):
        return Response(TopicSerializer(Topic.objects.all(), many=True).data)

    def retrieve(self, request, pk=None):
        topic = get_object_or_404(Topic, pk=pk)
        return Response(TopicSerializer(topic).data)

    def create(self, request):
        ser = TopicSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        topic = ser.save(user=request.user)
        for name in request.data.get("tags", []):
            tag, _ = Tag.objects.get_or_create(name=name)
            topic.tags.add(tag)
        return Response(TopicSerializer(topic).data, status=status.HTTP_201_CREATED)

    def update(self, request, pk=None):
        topic = get_object_or_404(Topic, pk=pk)
        self.check_object_permissions(request, topic)
        ser = TopicSerializer(topic, data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)

    def destroy(self, request, pk=None):
        topic = get_object_or_404(Topic, pk=pk)
        self.check_object_permissions(request, topic)
        topic.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def fetch_comments(pk):
    return [{"id": c.id, "content": c.content} for c in Comment.objects.filter(topic=pk)]


class CommentList(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, pk):
        return Response(fetch_comments(pk))

    def post(self, request, pk):
        topic = get_object_or_404(Topic, pk=pk)
        comment = Comment.objects.create(topic=topic, user=request.user,
                                         content=request.data.get("content", ""))
        return Response({"id": comment.id}, status=status.HTTP_201_CREATED)


class StatsView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request):
        return Response({"topics": Stats.count()})
