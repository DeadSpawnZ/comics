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
from django.urls import path, re_path
from django.conf import settings
from django.views.static import serve as serve_static
from comics.views import (
    collection, publishing, login, collectables, manage)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", login.login_view, name="login"),
    path("logout/", login.logout_view, name="logout"),
    path("ajax/get-publishing-date/<int:publishing_id>/", publishing.get_publishing_date, name="get_publishing_date"),
    path("ajax/get-comics/<int:publishing_id>/", publishing.get_comics_by_publishing, name="get_comics_by_publishing"),
    path("ajax/get-previous-trades/<int:comic_id>/", collection.get_previous_trades, name="get_previous_trades"),
    path("comics/", collection.comics_view, name="comics"),
    path("collectables/", collectables.collectables_view, name="collectables"),
    path("gestion/connectings/", manage.connecting_list, name="manage_connectings"),
    path("gestion/connectings/nuevo/", manage.connecting_editor, name="manage_connecting_new"),
    path("gestion/connectings/<int:pk>/", manage.connecting_editor, name="manage_connecting_edit"),
    path("gestion/api/comics/", manage.publishing_comics, name="manage_publishing_comics"),
]

# Los estaticos los sirve WhiteNoise (ver MIDDLEWARE), en dev y en "produccion" local.
# Media (imagenes subidas por usuarios) se sirve aqui de forma incondicional: este
# proyecto no tiene un servidor de estaticos/objectstore aparte para eso.
urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", serve_static, {"document_root": settings.MEDIA_ROOT}),
]
