import os

from django.urls import path

from drf_rel_toy.blog.views import Attach, Like, PostEdit, PostList, Star, TagPost

urlpatterns = [
    path("api/posts/", PostList.as_view()),
    path("api/posts/<int:pk>/", PostEdit.as_view()),
    path("api/posts/<int:pk>/tag/", TagPost.as_view()),
    path("api/posts/<int:pk>/star/", Star.as_view()),
    path("api/posts/<int:pk>/like/", Like.as_view()),
]
if os.environ.get("REL_TOY_UNRESOLVED") == "1":
    urlpatterns.append(path("api/posts/<int:pk>/attach/<str:field>/", Attach.as_view()))
