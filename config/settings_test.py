"""
Settings of the isolated test environment (Task 5). Everything runs against its own PostgreSQL database and its
own media folder, never against the application's data:

    python manage.py test apps --settings=config.settings_test --keepdb

The database and its role are created once by an administrator (see the validation documents); the role owns
only that database and has no CREATEDB permission, so Django reuses it with --keepdb instead of creating one.
It refuses to start if the test database is not configured or is the application's own database.
"""
import os

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F401,F403
from .settings import BASE_DIR, DATABASES

TEST_DB_NAME = os.getenv('TEST_DB_NAME', '')
if not TEST_DB_NAME or TEST_DB_NAME == DATABASES['default']['NAME']:
    raise ImproperlyConfigured('Configura TEST_DB_NAME con la base exclusiva de pruebas (distinta de la base de la '
                               'aplicación) antes de usar config.settings_test.')

DATABASES = {
    'default': {
        **DATABASES['default'],
        'NAME': TEST_DB_NAME,
        'USER': os.getenv('TEST_DB_USER', ''),
        'PASSWORD': os.getenv('TEST_DB_PASSWORD', ''),
        'CONN_MAX_AGE': 0,
        # The test runner uses this same, already created database (with --keepdb) instead of creating one
        'TEST': {'NAME': TEST_DB_NAME},
    }
}
if not DATABASES['default']['USER'] or DATABASES['default']['USER'] == os.getenv('DB_USER', 'maestro_app'):
    raise ImproperlyConfigured('Configura TEST_DB_USER con el usuario exclusivo de pruebas (no el de la aplicación).')

# Files written by tests or manual checks stay apart from the application's media
MEDIA_ROOT = BASE_DIR / 'media_pruebas'
# Fictitious data only: its own encryption key when one is configured for the test environment
FIELD_ENCRYPTION_KEY = os.getenv('TEST_FIELD_ENCRYPTION_KEY', FIELD_ENCRYPTION_KEY)  # noqa: F405
