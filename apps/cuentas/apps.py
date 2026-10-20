from django.apps import AppConfig


class CuentasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.cuentas'
    verbose_name = 'Cuentas de docentes'

    def ready(self):
        from . import signals  # noqa: F401
