from cryptography.fernet import Fernet
from django.conf import settings
from django.db import models


def _fernet():
    return Fernet(settings.FIELD_ENCRYPTION_KEY.encode())


class EncryptedTextField(models.TextField):
    """
    Text stored encrypted with Fernet (AES-128 + HMAC).

    PostgreSQL only holds the encrypted token; Django decrypts it on read.
    Every encryption is different, so this field can NOT be filtered or searched.
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ''):
            return value
        return _fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value in (None, ''):
            return value
        return _fernet().decrypt(value.encode()).decode()
