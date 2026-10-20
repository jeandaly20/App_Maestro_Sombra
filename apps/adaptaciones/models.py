import uuid

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models

from apps.estudiantes.models import Estudiante
from apps.materiales.models import MaterialOriginal
from apps.nucleo.choices import AjusteDUA, Condicion, FiltroCromatico


class EstadoAdaptacion(models.TextChoices):
    EN_REVISION = 'EN_REVISION', 'En revisión'
    APROBADA = 'APROBADA', 'Aprobada'
    EXPORTADA = 'EXPORTADA', 'Exportada'


def ruta_pdf(instance, filename):
    return f'adaptaciones/{instance.material.docente_id}/{instance.codigo}-v{instance.version}.pdf'


class AdaptacionCurricular(models.Model):
    """
    Guía adaptada por la IA para un estudiante y una de sus condiciones.
    Cada regeneración crea una versión nueva que apunta a la anterior.
    """

    Estado = EstadoAdaptacion

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    codigo = models.CharField(max_length=20, unique=True, editable=False)
    material = models.ForeignKey(
        MaterialOriginal, on_delete=models.CASCADE, related_name='adaptaciones'
    )
    estudiante = models.ForeignKey(
        Estudiante, on_delete=models.CASCADE, related_name='adaptaciones'
    )
    adaptacion_origen = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='regeneraciones'
    )
    version = models.PositiveSmallIntegerField(default=1)
    condicion = models.CharField(max_length=20, choices=Condicion.choices)
    titulo = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True)
    ajustes_aplicados = ArrayField(
        models.CharField(max_length=20, choices=AjusteDUA.choices), default=list, blank=True
    )
    filtro_cromatico = models.CharField(
        max_length=10, choices=FiltroCromatico.choices, default=FiltroCromatico.NINGUNO
    )
    contenido = models.JSONField()  # jsonb: guía reestructurada y descripción de figuras
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.EN_REVISION)
    pdf = models.FileField(upload_to=ruta_pdf, blank=True)
    modelo_ia = models.CharField(max_length=50, blank=True)
    tiempo_generacion_seg = models.PositiveIntegerField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'adaptación curricular'
        verbose_name_plural = 'adaptaciones curriculares'
        constraints = [
            models.CheckConstraint(
                check=models.Q(condicion__in=Condicion.values), name='adaptacion_condicion_valida'
            ),
            models.CheckConstraint(
                check=models.Q(estado__in=EstadoAdaptacion.values), name='adaptacion_estado_valido'
            ),
            models.CheckConstraint(
                check=models.Q(ajustes_aplicados__contained_by=AjusteDUA.values),
                name='adaptacion_ajustes_validos',
            ),
            models.CheckConstraint(
                check=models.Q(filtro_cromatico__in=FiltroCromatico.values),
                name='adaptacion_filtro_valido',
            ),
            models.CheckConstraint(check=models.Q(version__gte=1), name='adaptacion_version_positiva'),
            # Una versión no puede regenerarse de sí misma
            models.CheckConstraint(
                check=~models.Q(adaptacion_origen=models.F('id')), name='adaptacion_origen_distinto'
            ),
            models.UniqueConstraint(
                fields=['material', 'estudiante', 'condicion', 'version'],
                name='adaptacion_version_unica',
            ),
        ]

    def clean(self):
        if not (self.estudiante_id and self.material_id):
            return
        if self.estudiante.docente_id != self.material.docente_id:
            raise ValidationError({'estudiante': 'El estudiante no pertenece a este docente.'})
        if self.condicion not in self.estudiante.condiciones:
            raise ValidationError({'condicion': 'El estudiante no tiene registrada esta condición.'})

    def save(self, *args, **kwargs):
        if not self.codigo:
            # Ej.: ADAP-MAT-3F9A21 (materia + 6 caracteres del UUID)
            self.codigo = f'ADAP-{self.material.materia.abreviatura}-{self.id.hex[:6].upper()}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.codigo} · {self.titulo}'
