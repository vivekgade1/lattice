from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from drf_toy.blog.views import CommentList, StatsView, TagViewSet, TopicViewSet

router = DefaultRouter()
router.register("topics", TopicViewSet, basename="topic")
router.register("tags", TagViewSet, basename="tag")

urlpatterns = [
    path("api/", include(router.urls)),
    path("api/topics/<int:pk>/comments/", CommentList.as_view()),
    path("api/stats/", StatsView.as_view()),
    path("api/token/", TokenObtainPairView.as_view()),
    path("api/token/refresh/", TokenRefreshView.as_view()),
]
