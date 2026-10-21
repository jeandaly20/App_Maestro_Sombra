import uuid

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.choices import ColorFilter, Condition, UdlAdjustment
from apps.materials.models import OriginalMaterial
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
    created_at = models.DateTimeField('creado', auto_now_add=True)
    updated_at = models.DateTimeField('actualizado', auto_now=True)

    class Meta:
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
            models.UniqueConstraint(
                fields=['material', 'student', 'condition', 'version'],
                name='adaptation_version_unique',
            ),
        ]

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
