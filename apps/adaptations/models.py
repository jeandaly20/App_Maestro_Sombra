import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.choices import AdaptationGrade, ColorFilter, Condition, RecordStatus, UdlAdjustment
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
    Worksheet adapted by the AI for one student and one of their conditions.
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

    def clean(self):
        if not (self.student_id and self.material_id):
            return
        if self.student.teacher_id != self.material.teacher_id:
            raise ValidationError({'student': 'El estudiante no pertenece a este docente.'})
        if self.condition not in self.student.conditions:
            raise ValidationError({'condition': 'El estudiante no tiene registrada esta condición.'})

    def save(self, *args, **kwargs):
        if not self.code:
            # e.g. ADAP-MAT-3F9A21 (subject code + 6 characters of the UUID)
            self.code = f'ADAP-{self.material.subject.code}-{self.id.hex[:6].upper()}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.code} · {self.title}'


def lesson_plan_path(instance, filename):
    # The teacher's own file, stored under its UUID and only served back to them
    return f'lessons/{instance.teacher_id}/{instance.id}.xlsx'


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
    grade = models.CharField('grado de adaptación', max_length=16, choices=AdaptationGrade.choices)
    suggested_grade = models.CharField('grado sugerido por la IA', max_length=16, choices=AdaptationGrade.choices,
                                       blank=True)
    worksheet = models.ForeignKey(CurricularAdaptation, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='lesson_adaptations', verbose_name='ficha adaptada')
    content = models.JSONField('contenido')
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
            models.CheckConstraint(check=models.Q(grade__in=AdaptationGrade.values),
                                   name='lesson_adaptation_grade_valid'),
            models.CheckConstraint(check=models.Q(record_status__in=RecordStatus.values),
                                   name='lesson_adaptation_record_status_valid'),
        ]

    def __str__(self):
        return f'{self.lesson} · {self.student}'
