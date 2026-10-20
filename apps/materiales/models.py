import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models


class Materia(models.Model):
    """Catálogo fijo de asignaturas de EGB. El docente solo la selecciona."""
    nombre = models.CharField(max_length=60, unique=True)
    abreviatura = models.CharField(max_length=3, unique=True, help_text='Se usa en el código ADAP-MAT-…')

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class TipoEntrada(models.TextChoices):
    FOTO = 'FOTO', 'Foto del cuaderno o la pizarra'
    PDF = 'PDF', 'PDF'
    WORD = 'WORD', 'Word (.docx)'
    TEXTO = 'TEXTO', 'Texto escrito o pegado'


class EstadoMaterial(models.TextChoices):
    PENDIENTE = 'PENDIENTE', 'Pendiente'
    PROCESANDO = 'PROCESANDO', 'Procesando'
    PROCESADO = 'PROCESADO', 'Procesado'
    ERROR = 'ERROR', 'Error'


EXTENSIONES_POR_TIPO = {
    TipoEntrada.FOTO: ['jpg', 'jpeg', 'png', 'webp'],
    TipoEntrada.PDF: ['pdf'],
    TipoEntrada.WORD: ['docx'],
}
EXTENSIONES_MATERIAL = [ext for lista in EXTENSIONES_POR_TIPO.values() for ext in lista]


def validar_tamano(archivo):
    if archivo.size > settings.TAMANO_MAXIMO_MB * 1024 * 1024:
        raise ValidationError(f'El archivo supera los {settings.TAMANO_MAXIMO_MB} MB.')


def ruta_material(instance, filename):
    # En disco se guarda con el UUID; el nombre original queda solo en nombre_archivo
    extension = Path(filename).suffix.lower()
    return f'materiales/{instance.docente_id}/{instance.id}{extension}'


class MaterialOriginal(models.Model):
    """Guía que sube el docente (foto, PDF, Word o texto) y el análisis de Gemini."""

    Estado = EstadoMaterial

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    docente = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='materiales'
    )
    materia = models.ForeignKey(Materia, on_delete=models.PROTECT, related_name='materiales')
    titulo = models.CharField(max_length=150)
    tema = models.CharField(max_length=150, help_text='Ej.: El ciclo del agua. Da contexto a la IA.')
    tipo_entrada = models.CharField(max_length=5, choices=TipoEntrada.choices)
    archivo = models.FileField(
        upload_to=ruta_material,
        blank=True,
        validators=[FileExtensionValidator(EXTENSIONES_MATERIAL), validar_tamano],
    )
    nombre_archivo = models.CharField(max_length=255, blank=True)
    tamano_kb = models.PositiveIntegerField(null=True, blank=True)
    texto_ingresado = models.TextField(blank=True)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    analisis_ia = models.JSONField(null=True, blank=True)  # jsonb en PostgreSQL
    error_ia = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-creado']
        verbose_name = 'material original'
        verbose_name_plural = 'materiales originales'
        constraints = [
            models.CheckConstraint(
                check=models.Q(estado__in=EstadoMaterial.values), name='material_estado_valido'
            ),
            models.CheckConstraint(
                check=models.Q(tipo_entrada__in=TipoEntrada.values), name='material_tipo_valido'
            ),
            # Texto: debe venir el texto. Foto, PDF o Word: debe venir el archivo.
            models.CheckConstraint(
                check=(
                    models.Q(tipo_entrada=TipoEntrada.TEXTO) & ~models.Q(texto_ingresado='')
                ) | (
                    ~models.Q(tipo_entrada=TipoEntrada.TEXTO) & ~models.Q(archivo='')
                ),
                name='material_contenido_segun_tipo',
            ),
        ]

    def clean(self):
        if self.tipo_entrada in EXTENSIONES_POR_TIPO and self.archivo:
            extension = Path(self.archivo.name).suffix.lower().lstrip('.')
            if extension not in EXTENSIONES_POR_TIPO[self.tipo_entrada]:
                raise ValidationError({'archivo': 'El archivo no corresponde al tipo de entrada elegido.'})

    def __str__(self):
        return self.titulo
