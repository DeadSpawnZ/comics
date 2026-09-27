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
from django.urls import include, path, re_path
from django.views.i18n import JavaScriptCatalog
from django.conf import settings
from django.views.static import serve as serve_static
from django.views.generic import RedirectView
from comics.views import (
    collection, publishing, login, collectables, manage, manage_arcs, manage_editions, manage_publishings, my_comics)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("jsi18n/", JavaScriptCatalog.as_view(), name="javascript-catalog"),
    path("login/", login.login_view, name="login"),
    path("logout/", login.logout_view, name="logout"),
    path("ajax/get-publishing-date/<int:publishing_id>/", publishing.get_publishing_date, name="get_publishing_date"),
    path("ajax/get-comics/<int:publishing_id>/", publishing.get_comics_by_publishing, name="get_comics_by_publishing"),
    path("ajax/get-previous-trades/<int:edition_id>/", collection.get_previous_trades, name="get_previous_trades"),
    path("comics/", collection.comics_view, name="comics"),
    path("comics/ediciones/", my_comics.edition_list, name="comics_editions"),
    path("comics/ediciones/<int:pk>/", my_comics.edition_detail, name="comics_edition_detail"),
    path("comics/arcos/", my_comics.arc_list, name="comics_arcs"),
    path("comics/arcos/<int:pk>/", my_comics.arc_detail, name="comics_arc_detail"),
    path("comics/connectings/", my_comics.connecting_list, name="comics_connectings"),
    path("comics/connectings/<int:pk>/", my_comics.connecting_detail, name="comics_connecting_detail"),
    path("collectables/", collectables.collectables_view, name="collectables"),
    path("gestion/", RedirectView.as_view(pattern_name="manage_editions"), name="manage_home"),
    path("gestion/ediciones/", manage_editions.edition_list, name="manage_editions"),
    path("gestion/ediciones/nueva/", manage_editions.edition_form, name="manage_edition_new"),
    path("gestion/ediciones/<int:pk>/", manage_editions.edition_form, name="manage_edition_edit"),
    path("gestion/ediciones/<int:pk>/eliminar/", manage_editions.edition_delete, name="manage_edition_delete"),
    path("gestion/publishings/", manage_publishings.publishing_list, name="manage_publishings"),
    path("gestion/publishings/nuevo/", manage_publishings.publishing_form, name="manage_publishing_new"),
    path("gestion/publishings/<int:pk>/", manage_publishings.publishing_form, name="manage_publishing_edit"),
    path("gestion/publishings/<int:pk>/eliminar/", manage_publishings.publishing_delete, name="manage_publishing_delete"),
    path("gestion/api/issues/",manage_editions.publishing_issues, name="manage_publishing_issues"),
    path("gestion/arcos/", manage_arcs.arc_list, name="manage_arcs"),
    path("gestion/arcos/nuevo/", manage_arcs.arc_form, name="manage_arc_new"),
    path("gestion/arcos/<int:pk>/", manage_arcs.arc_form, name="manage_arc_edit"),
    path("gestion/arcos/<int:pk>/eliminar/", manage_arcs.arc_delete, name="manage_arc_delete"),
    path("gestion/connectings/", manage.connecting_list, name="manage_connectings"),
    path("gestion/connectings/nuevo/", manage.connecting_editor, name="manage_connecting_new"),
    path("gestion/connectings/<int:pk>/", manage.connecting_editor, name="manage_connecting_edit"),
    path("gestion/api/comics/", manage.publishing_comics, name="manage_publishing_comics"),
]

# Static files are served by WhiteNoise (see MIDDLEWARE), in dev and in local "production".
# Media (images uploaded by users) is served here unconditionally: this project
# has no separate static server/object store for it.
urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", serve_static, {"document_root": settings.MEDIA_ROOT}),
]
