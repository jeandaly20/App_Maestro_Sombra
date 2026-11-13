import uuid
from datetime import date

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.choices import (
    DISABILITY_SOURCES, INTELLECTUAL_DISABILITY, LEARNING_DISORDERS, Condition, DiagnosisSource, Grade,
    RecordStatus, group_of,
)
from apps.core.fields import EncryptedTextField

MAX_STUDENTS_PER_TEACHER = 3
UNSUPPORTED_COMBINATION = (
    'Esta versión de App Maestro Sombra todavía no contempla adaptaciones para combinaciones de dislexia o '
    'discalculia con discapacidad intelectual. Registra únicamente las condiciones admitidas por el sistema y '
    'consulta con el profesional responsable cuando el informe incluya una combinación no contemplada.'
)
MIN_AGE, MAX_AGE = 3, 18
DOCUMENT_TYPES = [('application/pdf', 'PDF'), ('image/jpeg', 'Imagen JPG'), ('image/png', 'Imagen PNG')]


class Student(models.Model):
    """
    Student with special educational needs (SEN). Identified by a pseudonym;
    the real name and notes are stored encrypted and never sent to the AI.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='students',
        verbose_name='docente',
    )
    pseudonym = models.CharField('seudónimo', max_length=20, unique=True, editable=False)
    name = EncryptedTextField('nombre', help_text='Se guarda cifrado.')
    birth_date = models.DateField('fecha de nacimiento')
    grade = models.CharField('grado', max_length=10, choices=Grade.choices)
    conditions = ArrayField(
        models.CharField(max_length=20, choices=Condition.choices),
        verbose_name='condiciones NEE',
        help_text='Una o varias condiciones (p. ej. dislexia y discalculia).',
    )
    diagnosis_source = models.CharField('respaldo del diagnóstico', max_length=16, choices=DiagnosisSource.choices,
                                        default=DiagnosisSource.IN_EVALUATION)
    diagnosis_date = models.DateField('fecha del informe', null=True, blank=True)
    # Optional encrypted copy of the document (see documents.py); only the storage path is kept here
    diagnosis_file = models.CharField('documento de respaldo (cifrado)', max_length=255, blank=True, editable=False)
    diagnosis_file_type = models.CharField('tipo de archivo', max_length=20, blank=True, editable=False,
                                           choices=DOCUMENT_TYPES)
    diagnosis_consent_at = models.DateTimeField('autorización del representante', null=True, blank=True,
                                                editable=False)
    # The authorization signed by the legal representative (the app's printable form), encrypted like the document
    consent_file = models.CharField('autorización firmada (cifrada)', max_length=255, blank=True, editable=False)
    consent_file_type = models.CharField('tipo de archivo de la autorización', max_length=20, blank=True,
                                         editable=False, choices=DOCUMENT_TYPES)
    # Result of the local reading of the document (documents.check_document); its text is never stored
    diagnosis_check = models.JSONField('revisión automática del documento', null=True, blank=True, editable=False)
    notes = EncryptedTextField('observaciones', blank=True, help_text='Se guardan cifradas.')
    # Individual pedagogical profile, written by the teacher for THIS student (not inferred from the NEE).
    # Encrypted like the notes; not sent to the AI yet.
    learning_strengths = EncryptedTextField('fortalezas del estudiante', blank=True, default='')
    learning_barriers = EncryptedTextField('barreras de aprendizaje observadas', blank=True, default='')
    pedagogical_supports = EncryptedTextField('apoyos pedagógicos individuales', blank=True, default='')
    record_status = models.CharField('estado del registro', max_length=8, choices=RecordStatus.choices,
                                     default=RecordStatus.ACTIVE)
    created_at = models.DateTimeField('creado', auto_now_add=True)
    updated_at = models.DateTimeField('actualizado', auto_now=True)

    class Meta:
        db_table = 'mst_student'
        ordering = ['grade', 'pseudonym']
        verbose_name = 'estudiante'
        verbose_name_plural = 'estudiantes'
        constraints = [
            # The database rejects unknown conditions even if they bypass Django
            models.CheckConstraint(
                check=models.Q(conditions__contained_by=Condition.values),
                name='student_conditions_valid',
            ),
            models.CheckConstraint(
                check=models.Q(conditions__len__gte=1), name='student_has_condition'
            ),
            models.CheckConstraint(check=models.Q(grade__in=Grade.values), name='student_grade_valid'),
            models.CheckConstraint(
                check=~(models.Q(conditions__overlap=LEARNING_DISORDERS)
                        & models.Q(conditions__overlap=INTELLECTUAL_DISABILITY)),
                name='student_no_learning_disorder_with_id',
            ),
            models.CheckConstraint(
                check=~models.Q(conditions__contains=INTELLECTUAL_DISABILITY), name='student_single_id_level'
            ),
            models.CheckConstraint(
                check=models.Q(diagnosis_source__in=DiagnosisSource.values), name='student_diagnosis_source_valid'
            ),
            # A declared document needs its date; an attached document already carries it
            models.CheckConstraint(
                check=models.Q(diagnosis_source=DiagnosisSource.IN_EVALUATION) | models.Q(diagnosis_date__isnull=False)
                | ~models.Q(diagnosis_file=''),
                name='student_diagnosis_has_date',
            ),
            # A stored document needs the representative's authorization and a real document type
            models.CheckConstraint(
                check=models.Q(diagnosis_file='') | models.Q(diagnosis_consent_at__isnull=False),
                name='student_document_has_consent',
            ),
            models.CheckConstraint(
                check=models.Q(diagnosis_file='') | ~models.Q(diagnosis_source=DiagnosisSource.IN_EVALUATION),
                name='student_document_needs_source',
            ),
            models.CheckConstraint(
                check=models.Q(diagnosis_file_type='') | models.Q(diagnosis_file_type__in=[t for t, _ in DOCUMENT_TYPES]),
                name='student_document_type_valid',
            ),
            # A stored signed authorization has a real file type and its date
            models.CheckConstraint(
                check=models.Q(consent_file='') | (models.Q(consent_file_type__in=[t for t, _ in DOCUMENT_TYPES])
                                                   & models.Q(diagnosis_consent_at__isnull=False)),
                name='student_consent_file_valid',
            ),
            models.CheckConstraint(
                check=models.Q(record_status__in=RecordStatus.values), name='student_record_status_valid'
            ),
        ]
        # The 3-active-students-per-teacher limit is also enforced by a PostgreSQL trigger

    @property
    def age(self):
        today = date.today()
        born = self.birth_date
        return today.year - born.year - ((today.month, today.day) < (born.month, born.day))

    @property
    def initials(self):
        words = self.name.split()
        return ''.join(w[0] for w in words[:2]).upper()

    @property
    def condition_labels(self):
        return [Condition(value).label for value in self.conditions]

    @property
    def condition_badges(self):
        """(label, group key) pairs, so each condition is colored by its own group."""
        return [(Condition(value).label, group_of(value).key) for value in self.conditions]

    @property
    def groups(self):
        """Condition groups of the student, without repeats and in catalog order."""
        found = []
        for condition in self.conditions:
            group = group_of(condition)
            if group not in found:
                found.append(group)
        return found

    @property
    def group_keys(self):
        return ' '.join(group.key for group in self.groups)

    @property
    def recommended_adjustments(self):
        """UDL adjustments recommended for all of the student's groups (shown as automatic guidelines)."""
        labels = []
        for group in self.groups:
            for label in group.adjustment_labels:
                if label not in labels:
                    labels.append(label)
        return labels

    def clean(self):
        if self.birth_date:
            if self.birth_date > date.today():
                raise ValidationError({'birth_date': 'La fecha no puede ser futura.'})
            if not MIN_AGE <= self.age <= MAX_AGE:
                raise ValidationError({'birth_date': f'La edad debe estar entre {MIN_AGE} y {MAX_AGE} años.'})
        chosen = set(self.conditions or [])
        if set(INTELLECTUAL_DISABILITY) <= chosen:
            raise ValidationError({'conditions': 'Elige un solo nivel de discapacidad intelectual: leve o moderada.'})
        # A limit of this version (its adaptation rules do not cover these combinations), not a clinical rule
        if chosen & set(LEARNING_DISORDERS) and chosen & set(INTELLECTUAL_DISABILITY):
            raise ValidationError({'conditions': UNSUPPORTED_COMBINATION})
        self.clean_diagnosis(chosen)
        if self.teacher_id and self._state.adding:
            registered = Student.objects.filter(teacher_id=self.teacher_id,
                                                record_status=RecordStatus.ACTIVE).count()
            if registered >= MAX_STUDENTS_PER_TEACHER:
                raise ValidationError(
                    f'Un docente puede registrar como máximo {MAX_STUDENTS_PER_TEACHER} estudiantes.'
                )

    def clean_diagnosis(self, chosen):
        if self.diagnosis_source == DiagnosisSource.IN_EVALUATION:
            if settings.DIAGNOSIS_DOCUMENT_REQUIRED:
                raise ValidationError({'diagnosis_source': 'Selecciona el documento que respalda el diagnóstico.'})
            if self.diagnosis_date:
                raise ValidationError({'diagnosis_date': (
                    'Si el estudiante está en proceso de evaluación aún no hay informe: deja la fecha vacía.'
                )})
            return
        attached = getattr(self, 'document_attached', self.has_document)
        if not self.diagnosis_date:
            if attached:  # the attached document already carries the date
                return self.check_disability_source(chosen)
            raise ValidationError({'diagnosis_date': 'Indica la fecha del informe o certificado, o adjunta el documento.'})
        if self.diagnosis_date > date.today():
            raise ValidationError({'diagnosis_date': 'La fecha del informe no puede ser futura.'})
        if self.birth_date and self.diagnosis_date < self.birth_date:
            raise ValidationError({'diagnosis_date': 'La fecha del informe no puede ser anterior al nacimiento.'})
        self.check_disability_source(chosen)

    def check_disability_source(self, chosen):
        allowed = DISABILITY_SOURCES + ([] if settings.DIAGNOSIS_DOCUMENT_REQUIRED else [DiagnosisSource.IN_EVALUATION])
        if chosen & set(INTELLECTUAL_DISABILITY) and self.diagnosis_source not in allowed:
            raise ValidationError({'diagnosis_source': (
                'La discapacidad intelectual se respalda con el certificado de calificación de discapacidad del MSP '
                '(o la cédula con la discapacidad registrada) '
                'o con el informe psicopedagógico de la UDAI.'
            )})

    @property
    def profile_sections(self):
        """How many of the three parts of the pedagogical profile are written (0 to 3)."""
        return sum(1 for text in (self.learning_strengths, self.learning_barriers, self.pedagogical_supports)
                   if (text or '').strip())

    @property
    def is_diagnosis_pending(self):
        return self.diagnosis_source == DiagnosisSource.IN_EVALUATION

    @property
    def has_document(self):
        return bool(self.diagnosis_file)

    @property
    def has_signed_consent(self):
        return bool(self.consent_file)

    @property
    def diagnosis_status(self):
        """Short label for the directory: how well the diagnosis is backed."""
        if self.has_document:
            return 'Documento adjunto'
        if self.is_diagnosis_pending:
            return 'En evaluación'
        return 'Solo declarado (sin documento)'

    @property
    def document_check(self):
        """(level, text) of the automatic reading: 'ok', 'warn' or 'manual'. It flags, it never proves."""
        check = self.diagnosis_check or {}
        if not self.has_document:
            return None
        if not check.get('readable'):
            return 'manual', 'No se pudo leer su texto (foto borrosa o escaneo de baja calidad): revísalo manualmente.'
        missing = [label for key, label in (('name', 'el nombre del estudiante'), ('institution', 'la institución'),
                                              ('diagnosis', 'el diagnóstico')) if not check.get(key)]
        # OCR can misread a photo, so its mismatches ask for a look instead of suggesting a wrong document
        source = ' (leído de la imagen)' if check.get('method') == 'ocr' else ''
        if not missing:
            return 'ok', f'Coincide el nombre, la institución y el diagnóstico{source}.'
        listed = ' y '.join([', '.join(missing[:-1]), missing[-1]]) if len(missing) > 1 else missing[0]
        return 'warn', f'No se encontró {listed} en el documento{source}. Revísalo.'

    def save(self, *args, **kwargs):
        if not self.pseudonym:
            self.pseudonym = f'EST-{self.id.hex[:6].upper()}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.pseudonym


# ---------------- Individual consent for each kind of data processing ----------------

class Treatment(models.TextChoices):
    """What the legal representative authorizes. Each one is separate: one never implies another."""
    INTERNAL = 'INTERNAL', 'Tratamiento interno para la gestión educativa'
    AI_ADAPTATION = 'AI_ADAPTATION', 'Envío a un proveedor de IA para generar adaptaciones (grado y NEE, con seudónimo)'
    AI_PROFILE = 'AI_PROFILE', 'Uso del perfil pedagógico (solo categorías) en solicitudes a un proveedor de IA'


class ConsentStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Vigente'
    REVOKED = 'REVOKED', 'Revocada'
    REPLACED = 'REPLACED', 'Reemplazada'


# The authorization of the registration form (apps/students/consent.py): its text covers keeping the data and
# sending the grade and the NEE to the AI, NOT the pedagogical profile
REGISTRATION_CONSENT_VERSION = 'REGISTRO-2026-v1'
REGISTRATION_TREATMENTS = [Treatment.INTERNAL, Treatment.AI_ADAPTATION]


class StudentConsent(models.Model):
    """
    One signed authorization of a student's legal representative: its document version, date, the treatments it
    covers and whether it is still in force. The signed file is stored encrypted (documents.py); only its storage
    path is kept here. A legal professional reviews the text: the app neither writes nor approves it.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='consents', verbose_name='estudiante')
    document_version = models.CharField('versión del documento', max_length=40)
    authorized_on = models.DateField('fecha de autorización')
    treatments = ArrayField(models.CharField(max_length=20, choices=Treatment.choices),
                            verbose_name='tratamientos autorizados')
    status = models.CharField('estado', max_length=8, choices=ConsentStatus.choices, default=ConsentStatus.ACTIVE)
    revoked_at = models.DateTimeField('revocada o reemplazada el', null=True, blank=True)
    signed_file = models.CharField('documento firmado (cifrado)', max_length=255, blank=True, editable=False)
    signed_file_type = models.CharField('tipo de archivo', max_length=20, blank=True, editable=False,
                                        choices=DOCUMENT_TYPES)
    created_at = models.DateTimeField('creado', auto_now_add=True)
    updated_at = models.DateTimeField('actualizado', auto_now=True)

    class Meta:
        db_table = 'trx_student_consent'
        ordering = ['-authorized_on', '-created_at']
        verbose_name = 'autorización del representante'
        verbose_name_plural = 'autorizaciones del representante'
        constraints = [
            models.CheckConstraint(check=models.Q(treatments__contained_by=Treatment.values),
                                   name='student_consent_treatments_valid'),
            models.CheckConstraint(check=models.Q(treatments__len__gte=1), name='student_consent_has_treatment'),
            models.CheckConstraint(check=models.Q(status__in=ConsentStatus.values), name='student_consent_status_valid'),
            # In force means not revoked; revoked or replaced always says when
            models.CheckConstraint(
                check=(models.Q(status=ConsentStatus.ACTIVE, revoked_at__isnull=True)
                       | (~models.Q(status=ConsentStatus.ACTIVE) & models.Q(revoked_at__isnull=False))),
                name='student_consent_revocation_dated'),
        ]

    @property
    def treatment_labels(self):
        return [Treatment(value).label for value in self.treatments]

    @property
    def is_active(self):
        return self.status == ConsentStatus.ACTIVE

    def __str__(self):
        return f'{self.student.pseudonym} · {self.document_version}'


def consent_allows(student, treatment):
    """Whether THIS student has an authorization in force that covers this treatment."""
    return student.consents.filter(status=ConsentStatus.ACTIVE, treatments__contains=[treatment]).exists()


def ai_profile_allowed(student):
    """
    The pedagogical profile may reach the AI only with both: the system setting turned on explicitly, and an
    individual authorization in force that covers it. Off by default; missing either keeps it out.
    """
    return bool(getattr(settings, 'AI_PROFILE_TRANSFER_ENABLED', False)) and \
        consent_allows(student, Treatment.AI_PROFILE)
