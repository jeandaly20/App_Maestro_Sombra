import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

logger = logging.getLogger('maestro_sombra')


def _client_ip(request):
    return request.META.get('REMOTE_ADDR', '') if request else ''


def masked_email(email):
    """
    An e-mail for the log without who it is (personal data, LOPDP): only its first letter and its domain,
    "d******@unemi.edu.ec", which still shows attacks on one account or one institution.
    """
    local, at, domain = (email or '').strip().lower().partition('@')
    if not local:
        return '(vacío)'
    return f'{local[0]}{"*" * min(len(local) - 1, 6)}{at}{domain}'


# Access event log (ISO/IEC 27001, control 8.15). Passwords are never logged, nor whole e-mails.

@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    logger.info('Sign-in: teacher=%s ip=%s', user.pk, _client_ip(request))


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user:
        logger.info('Sign-out: teacher=%s ip=%s', user.pk, _client_ip(request))


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    logger.warning('Failed sign-in: email=%s ip=%s', masked_email(credentials.get('username')), _client_ip(request))
