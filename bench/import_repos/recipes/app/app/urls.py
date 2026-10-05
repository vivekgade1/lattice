# ruff: noqa
"""app URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from django.conf import settings


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/user/', include('bench.import_repos.recipes.app.user.urls')),  # LATTICE MOD (was `path('api/user/', include('user.urls')),`: import path)
    path('api/recipe/', include('bench.import_repos.recipes.app.recipe.urls')),  # LATTICE MOD (was `path('api/recipe/', include('recipe.urls')),`: import path)
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
