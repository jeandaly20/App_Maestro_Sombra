import uuid

from django.db import models

from apps.adaptations.models import CurricularAdaptation
from apps.core.choices import RecordStatus
from apps.core.fields import EncryptedTextField


# The five questions of the classroom evaluation, each one backed by a source:
# 1. Goal Attainment Scaling, the standard way to measure an individual goal in special education
#    (Ruble et al., 2022): did the student reach the adapted objective, from -2 to +2.
# 2. Intensity of the support the student needed (the support-needs model behind the SIS-C).
# 3. Comprehension of the adapted instructions and material (UDL, principle of representation; CAST, 2024).
# 4. Signs of cognitive overload or fatigue (cognitive load theory).
# 5. Achievement in the official qualitative scale of Ecuador (Reglamento a la LOEI, 2023; Acuerdo
#    MINEDUC-MINEDUC-2023-00063-A), assessed with the adapted indicators (Acuerdo MINEDUC-MINEDUC-2023-00084-A).

class GoalAttainment(models.TextChoices):
    MUCH_LESS = 'MUCH_LESS', 'Mucho menos de lo esperado (−2)'
    LESS = 'LESS', 'Menos de lo esperado (−1)'
    EXPECTED = 'EXPECTED', 'Lo esperado (0)'
    MORE = 'MORE', 'Más de lo esperado (+1)'
    MUCH_MORE = 'MUCH_MORE', 'Mucho más de lo esperado (+2)'


GOAL_BELOW_EXPECTED = (GoalAttainment.MUCH_LESS, GoalAttainment.LESS)


class AutonomyLevel(models.TextChoices):
    """Intensity of the support the student needed during the class."""
    AUTONOMOUS = 'AUTONOMOUS', 'Sin apoyo (autónomo)'
    OCCASIONAL_SUPPORT = 'OCCASIONAL_SUPPORT', 'Apoyo puntual'
    FREQUENT_SUPPORT = 'FREQUENT_SUPPORT', 'Apoyo frecuente'
    CONSTANT_SUPPORT = 'CONSTANT_SUPPORT', 'Apoyo constante'


class Comprehension(models.TextChoices):
    YES = 'YES', 'Sí, con facilidad'
    DIFFICULTY = 'DIFFICULTY', 'Dificultad en palabras o símbolos'
    NO = 'NO', 'No comprendió'


class Overload(models.TextChoices):
    NONE = 'NONE', 'No'
    MILD = 'MILD', 'Leve'
    EVIDENT = 'EVIDENT', 'Evidente'


class AchievementLevel(models.TextChoices):
    """Ecuador's qualitative scale of learning achievement."""
    MASTERS = 'MASTERS', 'Domina los aprendizajes (9 a 10)'
    ACHIEVES = 'ACHIEVES', 'Alcanza los aprendizajes (7 a 8)'
    NEAR = 'NEAR', 'Está próximo a alcanzar (4 a 6)'
    NOT_YET = 'NOT_YET', 'No alcanza los aprendizajes (4 o menos)'


# Comprehension as a percentage, to average it on the dashboard
COMPREHENSION_SCORE = {'YES': 100, 'DIFFICULTY': 50, 'NO': 0}


class EvaluationStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Borrador'
    FINAL = 'FINAL', 'Finalizada'


class Outcome(models.TextChoices):
    """How the worksheet worked in class, derived from a final evaluation (bank badges and filters)."""
    PENDING = 'PENDING', 'Pendiente de evaluación'
    NEEDS_SIMPLER = 'NEEDS_SIMPLER', 'Requiere mayor simplificación'
    VERIFIED = 'VERIFIED', 'Verificado en aula'
    SUCCESS = 'SUCCESS', 'Comprendido con éxito'


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
    goal_attainment = models.CharField('logro del objetivo adaptado', max_length=10,
                                       choices=GoalAttainment.choices, blank=True)
    autonomy_level = models.CharField('nivel de apoyo', max_length=18,
                                      choices=AutonomyLevel.choices, blank=True)
    comprehension = models.CharField('comprensión', max_length=10, choices=Comprehension.choices, blank=True)
    overload = models.CharField('sobrecarga', max_length=8, choices=Overload.choices, blank=True)
    achievement_level = models.CharField('nivel de logro', max_length=8,
                                         choices=AchievementLevel.choices, blank=True)
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
                check=models.Q(goal_attainment='') | models.Q(goal_attainment__in=GoalAttainment.values),
                name='evaluation_goal_valid',
            ),
            models.CheckConstraint(
                check=models.Q(achievement_level='') | models.Q(achievement_level__in=AchievementLevel.values),
                name='evaluation_achievement_valid',
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

    @property
    def outcome(self):
        if self.status != EvaluationStatus.FINAL:
            return Outcome.PENDING
        if (self.goal_attainment in GOAL_BELOW_EXPECTED or self.comprehension == Comprehension.NO
                or self.overload == Overload.EVIDENT or self.achievement_level == AchievementLevel.NOT_YET
                or self.autonomy_level == AutonomyLevel.CONSTANT_SUPPORT):
            return Outcome.NEEDS_SIMPLER
        if (self.comprehension == Comprehension.YES and self.autonomy_level == AutonomyLevel.AUTONOMOUS
                and self.goal_attainment not in GOAL_BELOW_EXPECTED):
            return Outcome.SUCCESS
        return Outcome.VERIFIED

    def answers(self):
        """(question, answer) pairs in Spanish, as the teacher filled them in."""
        rows = [
            ('Logro del objetivo adaptado', self.get_goal_attainment_display()),
            ('Nivel de apoyo', self.get_autonomy_level_display()),
            ('Comprensión de las instrucciones y del material', self.get_comprehension_display()),
            ('Sobrecarga cognitiva o fatiga', self.get_overload_display()),
            ('Nivel de logro (escala del Ministerio de Educación)', self.get_achievement_level_display()),
        ]
        return [(question, answer) for question, answer in rows if answer]

    def __str__(self):
        return f'Evaluación de {self.adaptation.code}'
