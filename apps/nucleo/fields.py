from cryptography.fernet import Fernet
from django.conf import settings
from django.db import models


def _fernet():
    return Fernet(settings.FIELD_ENCRYPTION_KEY.encode())


class EncryptedTextField(models.TextField):
    """
    Texto que se guarda cifrado con Fernet (AES-128 + HMAC).

    En PostgreSQL solo queda el token cifrado; Django lo descifra al leerlo.
    Como cada cifrado es distinto, NO se puede filtrar ni buscar por este campo.
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
