"""
Request limits in the shared cache (settings.CACHES): how many times one address, or one e-mail, may use a form
that creates something or sends an e-mail (ISO/IEC 27001, control 8.6: capacity; and against abuse).
"""
from django.core.cache import cache


def client_ip(request):
    return request.META.get('REMOTE_ADDR', '') if request else ''


def over_limit(kind, who, limit):
    """True once `who` (an address or an e-mail) has used `kind` `limit` times in its window."""
    return cache.get(f'limit:{kind}:{who}', 0) >= limit


def count(kind, who, minutes):
    """One more use of `kind` by `who`; the window starts with the first use and lasts `minutes`."""
    key = f'limit:{kind}:{who}'
    if not cache.add(key, 1, minutes * 60):
        try:
            cache.incr(key)
        except ValueError:  # it expired just now
            cache.add(key, 1, minutes * 60)
