import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

logger = logging.getLogger('maestro_sombra')


def _ip(request):
    return request.META.get('REMOTE_ADDR', '') if request else ''


# Registro de eventos de acceso (ISO/IEC 27001, control 8.15). Nunca se guarda la contraseña.

@receiver(user_logged_in)
def ingreso_correcto(sender, request, user, **kwargs):
    logger.info('Inicio de sesión: docente=%s ip=%s', user.pk, _ip(request))


@receiver(user_logged_out)
def salida(sender, request, user, **kwargs):
    if user:
        logger.info('Cierre de sesión: docente=%s ip=%s', user.pk, _ip(request))


@receiver(user_login_failed)
def ingreso_fallido(sender, credentials, request=None, **kwargs):
    logger.warning('Inicio de sesión fallido: correo=%s ip=%s', credentials.get('username'), _ip(request))
