"""
Maestro Sombra settings.

Keys and credentials live in .env (never committed to Git).
Security controls follow ISO/IEC 27001:2022, Annex A (see docs/Normas_ISO.md).
Code and database are in English; everything the user sees is in Spanish.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# override=True: the dev server's auto-reload re-reads .env, so a new key takes effect without restarting
load_dotenv(BASE_DIR / '.env', override=True)

SECRET_KEY = os.environ['DJANGO_SECRET_KEY']

DEBUG = os.getenv('DJANGO_DEBUG', 'False') == 'True'

ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# Fernet key used to encrypt students' personal data (apps/core/fields.py)
FIELD_ENCRYPTION_KEY = os.environ['FIELD_ENCRYPTION_KEY']

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
# The proposal named gemini-1.5-flash, which Google retired. The "latest" alias always
# points to the current Flash model; the lite model is the fallback when it is overloaded.
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-flash-latest')
GEMINI_FALLBACK_MODEL = os.getenv('GEMINI_FALLBACK_MODEL', 'gemini-flash-lite-latest')
GEMINI_TIMEOUT_SECONDS = 60
# The main model gets less time: if it is slow (overloaded) the fallback answers sooner
GEMINI_PRIMARY_TIMEOUT_SECONDS = 25


# Applications

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',
    'apps.core',
    'apps.accounts',
    'apps.students',
    'apps.materials',
    'apps.adaptations',
    'apps.evaluations',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
            # {% asset %} is available in every template (versioned CSS and JS)
            'builtins': ['apps.core.templatetags.assets'],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database: PostgreSQL with a dedicated, non-admin user

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'maestro_sombra_app'),
        'USER': os.getenv('DB_USER', 'maestro_app'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'CONN_MAX_AGE': 60,
    }
}


# Authentication: teachers sign in with their email (accounts.Teacher)

AUTH_USER_MODEL = 'accounts.Teacher'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'login'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Session: 8 hours max (one school day). "Keep me signed in" extends it to 14 days.
SESSION_COOKIE_AGE = 60 * 60 * 8
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = True

# On the server (DEBUG=False) cookies only travel over HTTPS
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 0 if DEBUG else 60 * 60 * 24 * 30

# Security headers
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'


# Email (password reset). In development emails are printed to the console.

EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'Maestro Sombra <no-responder@maestrosombra.local>')
PASSWORD_RESET_TIMEOUT = 60 * 60  # the reset link expires after 1 hour


# Uploads: 10 MB max per worksheet

MAX_UPLOAD_MB = 10
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_MB * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
FILE_UPLOAD_PERMISSIONS = 0o640

# Diagnosis backing document. False (now, for testing): optional, and "in evaluation" is offered.
# True: every student must attach the UDAI, MSP, DECE or clinical document.
DIAGNOSIS_DOCUMENT_REQUIRED = os.getenv('DIAGNOSIS_DOCUMENT_REQUIRED', 'False').lower() in ('1', 'true', 'yes')

# Local OCR (Tesseract) to read photos and scanned diagnosis documents; nothing leaves the server.
# Empty: look for "tesseract" in PATH and in its default Windows folder. If it is missing, the
# document is marked for manual review.
TESSERACT_CMD = os.getenv('TESSERACT_CMD', '')
# Folder with the language models (spa.traineddata). Empty: Tesseract's own folder, or the user's
# %LOCALAPPDATA%/Tesseract-OCR/tessdata when Spanish was installed there without admin rights.
TESSERACT_DATA_DIR = os.getenv('TESSERACT_DATA_DIR', '')

# Dashboard: minutes a teacher needs to adapt one worksheet by hand. It is the reference for
# the "time saved" estimate and must be replaced by the value measured in the validation sprint.
MANUAL_ADAPTATION_MINUTES = int(os.getenv('MANUAL_ADAPTATION_MINUTES', '60'))


# Language and time zone. The interface is always in Spanish, whatever the
# browser language. Dates are stored in UTC (ISO 8601) and shown in Ecuador time.

LANGUAGE_CODE = 'es'
LANGUAGES = [('es', 'Español')]
TIME_ZONE = os.getenv('TIME_ZONE', 'America/Guayaquil')
USE_I18N = True
USE_TZ = True


# Static files (CSS, JS, fonts) and uploaded files

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Security event log (sign-ins, AI errors)

LOGS_DIR = BASE_DIR / 'logs'
LOGS_DIR.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'iso': {
            'format': '{asctime} {levelname} {name} {message}',
            'style': '{',
            'datefmt': '%Y-%m-%dT%H:%M:%S%z',
        },
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'iso'},
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOGS_DIR / 'maestro_sombra.log',
            'maxBytes': 5 * 1024 * 1024,
            'backupCount': 5,
            'encoding': 'utf-8',
            'formatter': 'iso',
        },
    },
    'loggers': {
        'django.security': {'handlers': ['console', 'file'], 'level': 'WARNING'},
        'maestro_sombra': {'handlers': ['console', 'file'], 'level': 'INFO'},
    },
}
