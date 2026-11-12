import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.choices import AdaptationGrade, ColorFilter, Condition, RecordStatus, UdlAdjustment


def conditions_label(conditions):
    """'DEA: Dislexia + TDAH': all the conditions a worksheet or a class was adapted for."""
    return ' + '.join(Condition(value).label for value in conditions)
from apps.materials.models import MaterialStatus, OriginalMaterial, Subject
from apps.students.models import Student


class AdaptationStatus(models.TextChoices):
    IN_REVIEW = 'IN_REVIEW', 'En revisión'
    APPROVED = 'APPROVED', 'Aprobada'
    EXPORTED = 'EXPORTED', 'Exportada'


def pdf_path(instance, filename):
    return f'adaptations/{instance.material.teacher_id}/{instance.code}-v{instance.version}.pdf'


class CurricularAdaptation(models.Model):
    """
    Worksheet adapted by the AI for one student, following the rules of all their conditions
    (conditions). "condition" keeps the first one: it groups the versions of the same worksheet.
    Each regeneration creates a new version that points to the previous one.
    """

    Status = AdaptationStatus

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField('código', max_length=20, unique=True, editable=False)
    material = models.ForeignKey(
        OriginalMaterial, on_delete=models.CASCADE, related_name='adaptations',
        verbose_name='material original',
    )
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name='adaptations', verbose_name='estudiante'
    )
    source_adaptation = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='regenerations',
        verbose_name='versión de origen',
    )
    version = models.PositiveSmallIntegerField('versión', default=1)
    condition = models.CharField('condición', max_length=20, choices=Condition.choices)
    conditions = ArrayField(models.CharField(max_length=20, choices=Condition.choices), default=list,
                            verbose_name='condiciones adaptadas')
    title = models.CharField('título', max_length=150)
    description = models.TextField('descripción', blank=True)
    applied_adjustments = ArrayField(
        models.CharField(max_length=20, choices=UdlAdjustment.choices),
        default=list, blank=True, verbose_name='ajustes DUA aplicados',
    )
    color_filter = models.CharField(
        'filtro cromático', max_length=10, choices=ColorFilter.choices, default=ColorFilter.NONE
    )
    content = models.JSONField('contenido')  # jsonb: restructured worksheet and figure descriptions
    status = models.CharField('estado', max_length=12, choices=Status.choices, default=Status.IN_REVIEW)
    pdf = models.FileField('PDF', upload_to=pdf_path, blank=True)
    ai_model = models.CharField('modelo de IA', max_length=50, blank=True)
    generation_time_sec = models.PositiveIntegerField('tiempo de generación (s)', null=True, blank=True)
    record_status = models.CharField('estado del registro', max_length=8, choices=RecordStatus.choices,
                                     default=RecordStatus.ACTIVE)
    created_at = models.DateTimeField('creado', auto_now_add=True)
    updated_at = models.DateTimeField('actualizado', auto_now=True)

    class Meta:
        db_table = 'trx_curricular_adaptation'
        ordering = ['-created_at']
        verbose_name = 'adaptación curricular'
        verbose_name_plural = 'adaptaciones curriculares'
        constraints = [
            models.CheckConstraint(
                check=models.Q(condition__in=Condition.values), name='adaptation_condition_valid'
            ),
            models.CheckConstraint(
                check=models.Q(conditions__contained_by=Condition.values) & models.Q(conditions__len__gte=1),
                name='adaptation_conditions_valid',
            ),
            models.CheckConstraint(
                check=models.Q(status__in=AdaptationStatus.values), name='adaptation_status_valid'
            ),
            models.CheckConstraint(
                check=models.Q(applied_adjustments__contained_by=UdlAdjustment.values),
                name='adaptation_adjustments_valid',
            ),
            models.CheckConstraint(
                check=models.Q(color_filter__in=ColorFilter.values), name='adaptation_color_filter_valid'
            ),
            models.CheckConstraint(check=models.Q(version__gte=1), name='adaptation_version_positive'),
            # A version cannot be regenerated from itself
            models.CheckConstraint(
                check=~models.Q(source_adaptation=models.F('id')), name='adaptation_source_not_self'
            ),
            models.CheckConstraint(
                check=models.Q(record_status__in=RecordStatus.values), name='adaptation_record_status_valid'
            ),
            models.UniqueConstraint(
                fields=['material', 'student', 'condition', 'version'],
                name='adaptation_version_unique',
            ),
        ]

    @property
    def adjustment_labels(self):
        return [UdlAdjustment(value).label for value in self.applied_adjustments]

    @property
    def conditions_label(self):
        return conditions_label(self.conditions or [self.condition])

    def clean(self):
        if not (self.student_id and self.material_id):
            return
        if self.student.teacher_id != self.material.teacher_id:
            raise ValidationError({'student': 'El estudiante no pertenece a este docente.'})
        if not set(self.conditions or [self.condition]) <= set(self.student.conditions):
            raise ValidationError({'conditions': 'El estudiante no tiene registrada esta condición.'})

    def save(self, *args, **kwargs):
        if not self.code:
            # e.g. ADAP-MAT-3F9A21 (subject code + 6 characters of the UUID)
            self.code = f'ADAP-{self.material.subject.code}-{self.id.hex[:6].upper()}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.code} · {self.title}'


PLAN_SUFFIXES = ('.xlsx', '.xls', '.docx', '.pdf')


def lesson_plan_path(instance, filename):
    # The teacher's own file (Excel, Word or PDF), stored under its UUID and only served back to them
    suffix = Path(filename).suffix.lower()
    return f'lessons/{instance.teacher_id}/{instance.id}{suffix if suffix in PLAN_SUFFIXES else ".xlsx"}'


def lesson_guide_path(instance, filename):
    return f'lessons/{instance.teacher_id}/{instance.id}-guia{Path(filename).suffix.lower()}'


class LessonPlan(models.Model):
    """
    "Adaptar clase": one session of the teacher's class plan (Excel), the student book pages of that
    class (kept as an OriginalMaterial, adapted like any worksheet) and, optionally, the teacher guide.
    """

    Status = MaterialStatus

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='lessons',
                                verbose_name='docente')
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, null=True, blank=True, related_name='lessons',
                                verbose_name='materia')
    material = models.ForeignKey(OriginalMaterial, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='lessons', verbose_name='páginas del libro')
    plan_file = models.FileField('planificación (Excel)', upload_to=lesson_plan_path)
    plan_file_name = models.CharField('nombre del archivo', max_length=255, blank=True)
    guide_file = models.FileField('guía del docente', upload_to=lesson_guide_path, blank=True)
    sheet = models.CharField('hoja', max_length=100, blank=True)
    session_row = models.PositiveIntegerField('fila de la sesión', default=0)
    session_label = models.CharField('sesión', max_length=100, blank=True)
    title = models.CharField('título de la clase', max_length=150, blank=True)
    session_text = models.TextField('texto de la sesión (sin datos personales)', blank=True)
    analysis = models.JSONField('análisis de la IA', null=True, blank=True)
    status = models.CharField('estado', max_length=10, choices=Status.choices, default=Status.PENDING)
    ai_error = models.TextField('error de la IA', blank=True)
    progress = models.CharField('avance', max_length=120, blank=True)  # what the AI is doing now
    record_status = models.CharField('estado del registro', max_length=8, choices=RecordStatus.choices,
                                     default=RecordStatus.ACTIVE)
    created_at = models.DateTimeField('creado', auto_now_add=True)
    updated_at = models.DateTimeField('actualizado', auto_now=True)

    class Meta:
        db_table = 'trx_lesson_plan'
        ordering = ['-created_at']
        verbose_name = 'clase planificada'
        verbose_name_plural = 'clases planificadas'
        constraints = [
            models.CheckConstraint(check=models.Q(status__in=MaterialStatus.values), name='lesson_status_valid'),
            models.CheckConstraint(check=models.Q(record_status__in=RecordStatus.values),
                                   name='lesson_record_status_valid'),
        ]

    @property
    def plan_format(self):
        """The format the adapted class is given back in: the same one the teacher uploaded (xlsx, pdf or docx)."""
        suffix = Path(self.plan_file_name or '').suffix.lower()
        return {'.pdf': 'pdf', '.docx': 'docx'}.get(suffix, 'xlsx')

    @property
    def plan_format_label(self):
        return {'pdf': 'PDF', 'docx': 'Word'}.get(self.plan_format, 'Excel')

    @property
    def is_week(self):
        """The teacher chose a whole week (all its sessions) instead of one session."""
        return self.session_label.lower().startswith('semana')

    def __str__(self):
        return f'{self.session_label} · {self.title}'.strip(' ·') or 'Clase'


class LessonAdaptation(models.Model):
    """The class adapted for one student: plan row for the coordinator, guide for the shadow teacher."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lesson = models.ForeignKey(LessonPlan, on_delete=models.CASCADE, related_name='adaptations',
                               verbose_name='clase')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='lesson_adaptations',
                                verbose_name='estudiante')
    condition = models.CharField('condición', max_length=20, choices=Condition.choices)
    conditions = ArrayField(models.CharField(max_length=20, choices=Condition.choices), default=list,
                            verbose_name='condiciones adaptadas')
    grade = models.CharField('grado de adaptación', max_length=16, choices=AdaptationGrade.choices)
    suggested_grade = models.CharField('grado sugerido por la IA', max_length=16, choices=AdaptationGrade.choices,
                                       blank=True)
    worksheet = models.ForeignKey(CurricularAdaptation, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='lesson_adaptations', verbose_name='ficha adaptada')
    content = models.JSONField('contenido')
    # The teacher reviews the adapted class (what goes in the Excel) before its worksheet and the download
    status = models.CharField('estado', max_length=12, choices=AdaptationStatus.choices,
                              default=AdaptationStatus.IN_REVIEW)
    ai_model = models.CharField('modelo de IA', max_length=50, blank=True)
    record_status = models.CharField('estado del registro', max_length=8, choices=RecordStatus.choices,
                                     default=RecordStatus.ACTIVE)
    created_at = models.DateTimeField('creado', auto_now_add=True)
    updated_at = models.DateTimeField('actualizado', auto_now=True)

    class Meta:
        db_table = 'trx_lesson_adaptation'
        ordering = ['student__created_at']
        verbose_name = 'clase adaptada'
        verbose_name_plural = 'clases adaptadas'
        constraints = [
            models.UniqueConstraint(fields=['lesson', 'student'], name='lesson_adaptation_unique'),
            models.CheckConstraint(check=models.Q(condition__in=Condition.values),
                                   name='lesson_adaptation_condition_valid'),
            models.CheckConstraint(check=models.Q(conditions__contained_by=Condition.values)
                                   & models.Q(conditions__len__gte=1),
                                   name='lesson_adaptation_conditions_valid'),
            models.CheckConstraint(check=models.Q(grade__in=AdaptationGrade.values),
                                   name='lesson_adaptation_grade_valid'),
            models.CheckConstraint(check=models.Q(status__in=AdaptationStatus.values),
                                   name='lesson_adaptation_status_valid'),
            models.CheckConstraint(check=models.Q(record_status__in=RecordStatus.values),
                                   name='lesson_adaptation_record_status_valid'),
        ]

    @property
    def conditions_label(self):
        return conditions_label(self.conditions or [self.condition])

    @property
    def active_worksheet(self):
        """Its worksheet, unless it was deleted from the bank."""
        worksheet = self.worksheet
        return worksheet if worksheet and worksheet.record_status == RecordStatus.ACTIVE else None

    @property
    def is_class_approved(self):
        return self.status != AdaptationStatus.IN_REVIEW

    @property
    def is_worksheet_approved(self):
        worksheet = self.active_worksheet
        return worksheet is None or worksheet.status != AdaptationStatus.IN_REVIEW

    @property
    def is_ready(self):
        """Class and worksheet reviewed: the student's folder can go in the ZIP."""
        return self.is_class_approved and self.is_worksheet_approved

    def __str__(self):
        return f'{self.lesson} · {self.student}'
