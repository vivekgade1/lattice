# ruff: noqa
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from bench.import_repos.recipes.app.recipe import views  # LATTICE MOD (was `from recipe import views`: import path)


router = DefaultRouter()
router.register('tags', views.TagViewSet)
router.register('ingredients', views.IngredientViewSet)
router.register('recipes', views.RecipeViewSet)

app_name = 'recipe'

urlpatterns = [
    path('', include(router.urls))
]
