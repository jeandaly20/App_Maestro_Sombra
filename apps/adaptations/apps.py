from django.apps import AppConfig


class AdaptationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.adaptations'
    verbose_name = 'Adaptaciones curriculares'

    def ready(self):
        from . import signals  # noqa: F401
