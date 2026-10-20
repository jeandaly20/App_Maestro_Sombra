import uuid
from datetime import date

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models

from apps.nucleo.choices import Condicion, Grado
from apps.nucleo.fields import EncryptedTextField

MAXIMO_ESTUDIANTES_POR_DOCENTE = 3


class Estudiante(models.Model):
    """
    Alumno con NEE. Se identifica con un seudónimo; el nombre real y las
    observaciones se guardan cifrados y nunca se envían a la IA.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    docente = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='estudiantes'
    )
    seudonimo = models.CharField(max_length=20, unique=True, editable=False)
    nombre = EncryptedTextField(help_text='Se guarda cifrado.')
    fecha_nacimiento = models.DateField()
    grado = models.CharField(max_length=10, choices=Grado.choices)
    condiciones = ArrayField(
        models.CharField(max_length=20, choices=Condicion.choices),
        help_text='Una o varias condiciones (p. ej. dislexia y discalculia).',
    )
    observaciones = EncryptedTextField(blank=True, help_text='Se guarda cifrado.')
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['grado', 'seudonimo']
        constraints = [
            # La BD rechaza condiciones que no existan, aunque no vengan desde Django
            models.CheckConstraint(
                check=models.Q(condiciones__contained_by=Condicion.values),
                name='estudiante_condiciones_validas',
            ),
            models.CheckConstraint(
                check=models.Q(condiciones__len__gte=1), name='estudiante_al_menos_una_condicion'
            ),
            models.CheckConstraint(
                check=models.Q(grado__in=Grado.values), name='estudiante_grado_valido'
            ),
        ]
        # El máximo de 3 estudiantes por docente también lo valida un trigger en PostgreSQL

    @property
    def edad(self):
        hoy = date.today()
        nacimiento = self.fecha_nacimiento
        return hoy.year - nacimiento.year - ((hoy.month, hoy.day) < (nacimiento.month, nacimiento.day))

    def clean(self):
        if self.fecha_nacimiento:
            if self.fecha_nacimiento > date.today():
                raise ValidationError({'fecha_nacimiento': 'La fecha no puede ser futura.'})
            if not 3 <= self.edad <= 18:
                raise ValidationError({'fecha_nacimiento': 'La edad debe estar entre 3 y 18 años.'})
        if self.docente_id and self._state.adding:
            registrados = Estudiante.objects.filter(docente_id=self.docente_id).count()
            if registrados >= MAXIMO_ESTUDIANTES_POR_DOCENTE:
                raise ValidationError(
                    f'Un docente puede registrar como máximo {MAXIMO_ESTUDIANTES_POR_DOCENTE} estudiantes.'
                )

    def save(self, *args, **kwargs):
        if not self.seudonimo:
            self.seudonimo = f'EST-{self.id.hex[:6].upper()}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.seudonimo
