import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower


class Especialidad(models.TextChoices):
    EDUCACION_ESPECIAL = 'EDUCACION_ESPECIAL', 'Educación especial / inclusiva'
    MAESTRO_SOMBRA = 'MAESTRO_SOMBRA', 'Acompañamiento pedagógico / maestro sombra'
    PSICOPEDAGOGIA = 'PSICOPEDAGOGIA', 'Psicopedagogía y orientación (DECE/UDAI)'
    EDUCACION_INICIAL = 'EDUCACION_INICIAL', 'Educación inicial'
    EGB = 'EGB', 'Educación General Básica'
    BACHILLERATO = 'BACHILLERATO', 'Bachillerato'


class DocenteManager(BaseUserManager):
    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        # Todo el correo en minúsculas: Ana@Mail.com y ana@mail.com son la misma cuenta
        return (email or '').strip().lower()

    def get_by_natural_key(self, email):
        return self.get(email__iexact=email)

    def _crear(self, email, password, **extra):
        if not email:
            raise ValueError('El correo es obligatorio.')
        docente = self.model(email=self.normalize_email(email), **extra)
        docente.set_password(password)  # se guarda el hash PBKDF2, nunca el texto
        docente.save(using=self._db)
        return docente

    def create_user(self, email, password=None, **extra):
        extra.setdefault('is_staff', False)
        extra.setdefault('is_superuser', False)
        return self._crear(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra['is_staff'] = True
        extra['is_superuser'] = True
        return self._crear(email, password, **extra)


class Docente(AbstractUser):
    """
    Usuario del sistema. Inicia sesión con su correo.
    Docente: usuario normal. Administrador: is_staff (entra a /admin/).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None
    first_name = None
    last_name = None
    nombre_completo = models.CharField(max_length=150)
    email = models.EmailField('correo electrónico', unique=True)
    especialidad = models.CharField(max_length=20, choices=Especialidad.choices, blank=True)
    acepta_terminos = models.BooleanField(default=False)
    fecha_aceptacion_terminos = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['nombre_completo']

    objects = DocenteManager()

    class Meta:
        ordering = ['nombre_completo']
        constraints = [
            # Junto con unique, impide duplicados que solo cambian en mayúsculas
            models.CheckConstraint(check=models.Q(email=Lower('email')), name='docente_email_minusculas'),
            models.CheckConstraint(
                check=models.Q(especialidad='') | models.Q(especialidad__in=Especialidad.values),
                name='docente_especialidad_valida',
            ),
            # Si aceptó los términos, debe quedar registrado cuándo (LOPDP, consentimiento)
            models.CheckConstraint(
                check=models.Q(acepta_terminos=False) | models.Q(fecha_aceptacion_terminos__isnull=False),
                name='docente_terminos_con_fecha',
            ),
        ]

    def get_full_name(self):
        return self.nombre_completo

    def get_short_name(self):
        return self.nombre_completo.split(' ')[0] if self.nombre_completo else self.email

    def __str__(self):
        return self.nombre_completo or self.email
