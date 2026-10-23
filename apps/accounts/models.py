import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower

from apps.core.choices import RecordStatus


class Specialty(models.TextChoices):
    SPECIAL_EDUCATION = 'SPECIAL_EDUCATION', 'Educación especial / inclusiva'
    SHADOW_TEACHER = 'SHADOW_TEACHER', 'Acompañamiento pedagógico / maestro sombra'
    PSYCHOPEDAGOGY = 'PSYCHOPEDAGOGY', 'Psicopedagogía y orientación (DECE/UDAI)'
    EARLY_EDUCATION = 'EARLY_EDUCATION', 'Educación inicial'
    BASIC_EDUCATION = 'BASIC_EDUCATION', 'Educación General Básica'
    HIGH_SCHOOL = 'HIGH_SCHOOL', 'Bachillerato'


class TeacherManager(BaseUserManager):
    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        # Whole address in lowercase: Ana@Mail.com and ana@mail.com are the same account
        return (email or '').strip().lower()

    def get_by_natural_key(self, email):
        return self.get(email__iexact=email)

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError('El correo es obligatorio.')
        teacher = self.model(email=self.normalize_email(email), **extra)
        teacher.set_password(password)  # stores the PBKDF2 hash, never the plain text
        teacher.save(using=self._db)
        return teacher

    def create_user(self, email, password=None, **extra):
        extra.setdefault('is_staff', False)
        extra.setdefault('is_superuser', False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra['is_staff'] = True
        extra['is_superuser'] = True
        return self._create(email, password, **extra)


class Teacher(AbstractUser):
    """
    System user. Signs in with an email address.
    Teacher: regular user. Administrator: is_staff (can open /admin/).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None
    first_name = None
    last_name = None
    full_name = models.CharField('nombre completo', max_length=150)
    email = models.EmailField('correo electrónico', unique=True)
    specialty = models.CharField('especialidad', max_length=20, choices=Specialty.choices, blank=True)
    accepted_terms = models.BooleanField('aceptó los términos', default=False)
    terms_accepted_at = models.DateTimeField('fecha de aceptación de términos', null=True, blank=True)
    record_status = models.CharField('estado del registro', max_length=8, choices=RecordStatus.choices,
                                     default=RecordStatus.ACTIVE)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name']

    objects = TeacherManager()

    class Meta:
        db_table = 'mst_teacher'
        ordering = ['full_name']
        verbose_name = 'docente'
        verbose_name_plural = 'docentes'
        constraints = [
            # Together with unique, blocks duplicates that only differ in case
            models.CheckConstraint(check=models.Q(email=Lower('email')), name='teacher_email_lowercase'),
            models.CheckConstraint(
                check=models.Q(specialty='') | models.Q(specialty__in=Specialty.values),
                name='teacher_specialty_valid',
            ),
            # Consent must record when it was given (LOPDP)
            models.CheckConstraint(
                check=models.Q(accepted_terms=False) | models.Q(terms_accepted_at__isnull=False),
                name='teacher_terms_have_date',
            ),
            models.CheckConstraint(
                check=models.Q(record_status__in=RecordStatus.values), name='teacher_record_status_valid'
            ),
        ]

    def save(self, *args, **kwargs):
        # Django blocks the sign-in of inactive users through is_active, so both stay in step
        self.is_active = self.record_status == RecordStatus.ACTIVE
        super().save(*args, **kwargs)

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(' ')[0] if self.full_name else self.email

    @property
    def initials(self):
        words = self.full_name.split()
        return ''.join(w[0] for w in words[:2]).upper() or self.email[0].upper()

    def __str__(self):
        return self.full_name or self.email
