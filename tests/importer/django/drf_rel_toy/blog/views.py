from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_rel_toy.blog.models import Post, Tag


class PostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ["id", "title", "categories"]


class PostList(APIView):
    def get(self, request):
        return Response([{"id": p.id, "title": p.title} for p in Post.objects.all()])


class PostEdit(APIView):
    def put(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        ser = PostSerializer(post, data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)


class TagPost(APIView):
    def post(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        tags = post.tags
        tags.add(get_object_or_404(Tag, name=request.data.get("name")))
        return Response({"id": post.id})


class Star(APIView):
    def post(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        post.star(request.user)
        return Response({"id": post.id})


class Like(APIView):
    def post(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        post.likers.add(request.user)
        return Response({"id": post.id})


class Attach(APIView):
    def post(self, request, pk, field):
        post = get_object_or_404(Post, pk=pk)
        getattr(post, field).add(request.data.get("item"))
        return Response({"id": post.id})
