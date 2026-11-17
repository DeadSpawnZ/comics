from django.apps import AppConfig
from django.contrib.admin.apps import AdminConfig


class ComicsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "comics"

    def ready(self) -> None:
        from comics import signals  # noqa: F401  (connects the receivers)
        from comics.achievements import rules  # noqa: F401  (registers the achievement rules)


class ComiAdminConfig(AdminConfig):
    """django.contrib.admin with ComiAdminSite (superusers only) as `admin.site`."""

    default_site = "comics.admin_site.ComiAdminSite"
