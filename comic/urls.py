"""
URL configuration for comic project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
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
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from comics.views import collection, publishing, login

urlpatterns = [
    path("admin/", admin.site.urls),
    path("collection/", collection.all, name="collectable_all"),
    path(
        "collection/<int:collectable_id>/", collection.collectable, name="collectable"
    ),
    path("publishing/", publishing.all, name="all"),
    path("publishing/<int:publishing_id>/", publishing.publishing, name="publishing"),
    path("login/", login.login_view, name="login"),
    path("logout/", login.logout_view, name="logout"),
]

urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
