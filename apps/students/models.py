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
    # Result of the local reading of the document (documents.check_document); its text is never stored
    diagnosis_check = models.JSONField('revisión automática del documento', null=True, blank=True, editable=False)
    notes = EncryptedTextField('observaciones', blank=True, help_text='Se guardan cifradas.')
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
            if not 3 <= self.age <= 18:
                raise ValidationError({'birth_date': 'La edad debe estar entre 3 y 18 años.'})
        chosen = set(self.conditions or [])
        if set(INTELLECTUAL_DISABILITY) <= chosen:
            raise ValidationError({'conditions': 'Elige un solo nivel de discapacidad intelectual: leve o moderada.'})
        if chosen & set(LEARNING_DISORDERS) and chosen & set(INTELLECTUAL_DISABILITY):
            raise ValidationError({'conditions': (
                'La dislexia y la discalculia (DEA) no se registran junto con discapacidad intelectual: el DSM-5 pide '
                'descartar la discapacidad intelectual antes de diagnosticar un DEA. Elige el diagnóstico del informe.'
            )})
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
            self.diagnosis_date = None
            return
        # The form marks whether a document is (or stays) attached; without one the date is required
        attached = getattr(self, 'document_attached', self.has_document)
        if not self.diagnosis_date:
            if attached:
                return self.check_disability_source(chosen)
            raise ValidationError({'diagnosis_date': 'Indica la fecha del informe o certificado, o adjunta el documento.'})
        if self.diagnosis_date > date.today():
            raise ValidationError({'diagnosis_date': 'La fecha del informe no puede ser futura.'})
        if self.birth_date and self.diagnosis_date < self.birth_date:
            raise ValidationError({'diagnosis_date': 'La fecha del informe no puede ser anterior al nacimiento.'})
        self.check_disability_source(chosen)

    def check_disability_source(self, chosen):
        if chosen & set(INTELLECTUAL_DISABILITY) and self.diagnosis_source not in DISABILITY_SOURCES:
            raise ValidationError({'diagnosis_source': (
                'La discapacidad intelectual se respalda con el certificado o carné de discapacidad del MSP '
                'o con el informe psicopedagógico de la UDAI.'
            )})

    @property
    def is_diagnosis_pending(self):
        return self.diagnosis_source == DiagnosisSource.IN_EVALUATION

    @property
    def has_document(self):
        return bool(self.diagnosis_file)

    @property
    def diagnosis_status(self):
        """Short label for the directory: how well the diagnosis is backed."""
        if self.is_diagnosis_pending:
            return 'En evaluación'
        return 'Documento adjunto' if self.has_document else 'Solo declarado'

    @property
    def document_check(self):
        """(level, text) of the automatic reading: 'ok', 'warn' or 'manual'. It flags, it never proves."""
        check = self.diagnosis_check or {}
        if not self.has_document:
            return None
        if not check.get('readable'):
            return 'manual', 'No se pudo leer su texto (foto o escaneo): revísalo manualmente.'
        missing = [label for key, label in (('name', 'el nombre del estudiante'), ('institution', 'la institución'),
                                              ('diagnosis', 'el diagnóstico')) if not check.get(key)]
        if not missing:
            return 'ok', 'Coincide el nombre, la institución y el diagnóstico.'
        return 'warn', f'No se encontró {", ".join(missing)} en el documento. Revísalo.'

    def save(self, *args, **kwargs):
        if not self.pseudonym:
            self.pseudonym = f'EST-{self.id.hex[:6].upper()}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.pseudonym
