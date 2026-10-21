import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

logger = logging.getLogger('maestro_sombra')


def _client_ip(request):
    return request.META.get('REMOTE_ADDR', '') if request else ''


# Access event log (ISO/IEC 27001, control 8.15). Passwords are never logged.

@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    logger.info('Sign-in: teacher=%s ip=%s', user.pk, _client_ip(request))


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user:
        logger.info('Sign-out: teacher=%s ip=%s', user.pk, _client_ip(request))


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    logger.warning('Failed sign-in: email=%s ip=%s', credentials.get('username'), _client_ip(request))
