# ruff: noqa
from django.urls import path

from bench.import_repos.recipes.app.user import views  # LATTICE MOD (was `from user import views`: import path)


app_name = 'user'

urlpatterns = [
    path('create/', views.CreateUserView.as_view(), name='create'),
    path('token/', views.CreateTokenView.as_view(), name='token'),
    path('me/', views.ManageUserView.as_view(), name='me'),
]
