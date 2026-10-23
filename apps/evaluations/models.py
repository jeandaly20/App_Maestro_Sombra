import uuid

from django.db import models

from apps.adaptations.models import CurricularAdaptation
from apps.core.choices import RecordStatus
from apps.core.fields import EncryptedTextField


class AutonomyLevel(models.TextChoices):
    AUTONOMOUS = 'AUTONOMOUS', 'Totalmente autónomo'
    OCCASIONAL_SUPPORT = 'OCCASIONAL_SUPPORT', 'Con apoyo puntual'
    CONSTANT_SUPPORT = 'CONSTANT_SUPPORT', 'Apoyo constante'


class Comprehension(models.TextChoices):
    YES = 'YES', 'Sí, con facilidad'
    DIFFICULTY = 'DIFFICULTY', 'Dificultad en palabras o símbolos'
    NO = 'NO', 'No comprendió'


class Overload(models.TextChoices):
    NONE = 'NONE', 'No'
    MILD = 'MILD', 'Leve'
    EVIDENT = 'EVIDENT', 'Evidente'


class AccuracyRange(models.TextChoices):
    BELOW_50 = 'BELOW_50', 'Menos del 50 %'
    BETWEEN_50_75 = 'BETWEEN_50_75', 'Entre 50 % y 75 %'
    ABOVE_75 = 'ABOVE_75', 'Más del 75 %'


class EvaluationStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Borrador'
    FINAL = 'FINAL', 'Finalizada'


class Evaluation(models.Model):
    """
    What the teacher observes after using the worksheet in class. Backs the
    evaluation form and the bank's quick record (which only asks autonomy and comprehension).
    """

    Status = EvaluationStatus

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    adaptation = models.OneToOneField(
        CurricularAdaptation, on_delete=models.CASCADE, related_name='evaluation',
        verbose_name='adaptación',
    )
    autonomy_level = models.CharField('nivel de autonomía', max_length=18,
                                      choices=AutonomyLevel.choices, blank=True)
    comprehension = models.CharField('comprensión', max_length=10, choices=Comprehension.choices, blank=True)
    overload = models.CharField('sobrecarga', max_length=8, choices=Overload.choices, blank=True)
    accuracy_range = models.CharField('rango de acierto', max_length=13,
                                      choices=AccuracyRange.choices, blank=True)
    observation = EncryptedTextField('observación', blank=True, help_text='Se guarda cifrada.')
    regeneration_requested = models.BooleanField('solicita regeneración', default=False)
    status = models.CharField('estado', max_length=5, choices=Status.choices, default=Status.DRAFT)
    ai_recommendation = models.TextField('recomendación de la IA', blank=True)
    recommended_at = models.DateTimeField('fecha de la recomendación', null=True, blank=True)
    record_status = models.CharField('estado del registro', max_length=8, choices=RecordStatus.choices,
                                     default=RecordStatus.ACTIVE)
    created_at = models.DateTimeField('creado', auto_now_add=True)
    updated_at = models.DateTimeField('actualizado', auto_now=True)

    class Meta:
        db_table = 'trx_evaluation'
        ordering = ['-updated_at']
        verbose_name = 'evaluación'
        verbose_name_plural = 'evaluaciones'
        constraints = [
            models.CheckConstraint(
                check=models.Q(status__in=EvaluationStatus.values), name='evaluation_status_valid'
            ),
            models.CheckConstraint(
                check=models.Q(autonomy_level='') | models.Q(autonomy_level__in=AutonomyLevel.values),
                name='evaluation_autonomy_valid',
            ),
            models.CheckConstraint(
                check=models.Q(comprehension='') | models.Q(comprehension__in=Comprehension.values),
                name='evaluation_comprehension_valid',
            ),
            models.CheckConstraint(
                check=models.Q(overload='') | models.Q(overload__in=Overload.values),
                name='evaluation_overload_valid',
            ),
            models.CheckConstraint(
                check=models.Q(accuracy_range='') | models.Q(accuracy_range__in=AccuracyRange.values),
                name='evaluation_accuracy_valid',
            ),
            # A draft may be incomplete; a final evaluation may not
            models.CheckConstraint(
                check=models.Q(status=EvaluationStatus.DRAFT)
                | (~models.Q(autonomy_level='') & ~models.Q(comprehension='')),
                name='evaluation_final_is_complete',
            ),
            models.CheckConstraint(
                check=models.Q(ai_recommendation='') | models.Q(recommended_at__isnull=False),
                name='evaluation_recommendation_has_date',
            ),
            models.CheckConstraint(
                check=models.Q(record_status__in=RecordStatus.values), name='evaluation_record_status_valid'
            ),
        ]

    def __str__(self):
        return f'Evaluación de {self.adaptation.code}'
