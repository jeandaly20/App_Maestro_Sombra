import uuid

from django.db import models

from apps.adaptaciones.models import AdaptacionCurricular
from apps.nucleo.fields import EncryptedTextField


class NivelAutonomia(models.TextChoices):
    AUTONOMO = 'AUTONOMO', 'Totalmente autónomo'
    APOYO_PUNTUAL = 'APOYO_PUNTUAL', 'Con apoyo puntual'
    APOYO_CONSTANTE = 'APOYO_CONSTANTE', 'Apoyo constante'


class Comprension(models.TextChoices):
    SI = 'SI', 'Sí, con facilidad'
    DIFICULTAD = 'DIFICULTAD', 'Dificultad en palabras o símbolos'
    NO = 'NO', 'No comprendió'


class Sobrecarga(models.TextChoices):
    NO = 'NO', 'No'
    LEVE = 'LEVE', 'Leve'
    EVIDENTE = 'EVIDENTE', 'Evidente'


class RangoAcierto(models.TextChoices):
    MENOS_50 = 'MENOS_50', 'Menos del 50 %'
    ENTRE_50_75 = 'ENTRE_50_75', 'Entre 50 % y 75 %'
    MAS_75 = 'MAS_75', 'Más del 75 %'


class EstadoEvaluacion(models.TextChoices):
    BORRADOR = 'BORRADOR', 'Borrador'
    FINALIZADA = 'FINALIZADA', 'Finalizada'


class Evaluacion(models.Model):
    """
    Lo que el docente observa al aplicar la guía en clase. Sirve el formulario
    de evaluación y el registro rápido del banco (que solo pide autonomía y comprensión).
    """

    Estado = EstadoEvaluacion

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    adaptacion = models.OneToOneField(
        AdaptacionCurricular, on_delete=models.CASCADE, related_name='evaluacion'
    )
    nivel_autonomia = models.CharField(max_length=15, choices=NivelAutonomia.choices, blank=True)
    comprension = models.CharField(max_length=10, choices=Comprension.choices, blank=True)
    sobrecarga = models.CharField(max_length=8, choices=Sobrecarga.choices, blank=True)
    rango_acierto = models.CharField(max_length=11, choices=RangoAcierto.choices, blank=True)
    observacion = EncryptedTextField(blank=True, help_text='Se guarda cifrada.')
    solicita_regeneracion = models.BooleanField(default=False)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.BORRADOR)
    recomendacion_ia = models.TextField(blank=True)
    fecha_recomendacion = models.DateTimeField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-actualizado']
        verbose_name = 'evaluación'
        verbose_name_plural = 'evaluaciones'
        constraints = [
            models.CheckConstraint(
                check=models.Q(estado__in=EstadoEvaluacion.values), name='evaluacion_estado_valido'
            ),
            models.CheckConstraint(
                check=models.Q(nivel_autonomia='') | models.Q(nivel_autonomia__in=NivelAutonomia.values),
                name='evaluacion_autonomia_valida',
            ),
            models.CheckConstraint(
                check=models.Q(comprension='') | models.Q(comprension__in=Comprension.values),
                name='evaluacion_comprension_valida',
            ),
            models.CheckConstraint(
                check=models.Q(sobrecarga='') | models.Q(sobrecarga__in=Sobrecarga.values),
                name='evaluacion_sobrecarga_valida',
            ),
            models.CheckConstraint(
                check=models.Q(rango_acierto='') | models.Q(rango_acierto__in=RangoAcierto.values),
                name='evaluacion_rango_valido',
            ),
            # Un borrador puede quedar incompleto; una evaluación finalizada no
            models.CheckConstraint(
                check=models.Q(estado=EstadoEvaluacion.BORRADOR)
                | (~models.Q(nivel_autonomia='') & ~models.Q(comprension='')),
                name='evaluacion_finalizada_completa',
            ),
            models.CheckConstraint(
                check=models.Q(recomendacion_ia='') | models.Q(fecha_recomendacion__isnull=False),
                name='evaluacion_recomendacion_con_fecha',
            ),
        ]

    def __str__(self):
        return f'Evaluación de {self.adaptacion.codigo}'
