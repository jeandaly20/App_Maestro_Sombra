import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models


class Subject(models.Model):
    """Fixed catalog of EGB subjects. Teachers only pick one."""
    name = models.CharField('nombre', max_length=60, unique=True)
    code = models.CharField('abreviatura', max_length=3, unique=True,
                            help_text='Se usa en el código ADAP-MAT-…')

    class Meta:
        ordering = ['name']
        verbose_name = 'materia'
        verbose_name_plural = 'materias'

    def __str__(self):
        return self.name


class InputType(models.TextChoices):
    PHOTO = 'PHOTO', 'Foto del cuaderno o la pizarra'
    PDF = 'PDF', 'PDF'
    WORD = 'WORD', 'Word (.docx)'
    TEXT = 'TEXT', 'Texto escrito o pegado'


class MaterialStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pendiente'
    PROCESSING = 'PROCESSING', 'Procesando'
    PROCESSED = 'PROCESSED', 'Procesado'
    ERROR = 'ERROR', 'Error'


EXTENSIONS_BY_TYPE = {
    InputType.PHOTO: ['jpg', 'jpeg', 'png', 'webp'],
    InputType.PDF: ['pdf'],
    InputType.WORD: ['docx'],
}
ALLOWED_EXTENSIONS = [ext for extensions in EXTENSIONS_BY_TYPE.values() for ext in extensions]


def validate_size(file):
    if file.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f'El archivo supera los {settings.MAX_UPLOAD_MB} MB.')


def material_path(instance, filename):
    # Stored on disk under its UUID; the original name is kept only in file_name
    extension = Path(filename).suffix.lower()
    return f'materials/{instance.teacher_id}/{instance.id}{extension}'


class OriginalMaterial(models.Model):
    """Worksheet uploaded by the teacher (photo, PDF, Word or text) and Gemini's analysis."""

    Status = MaterialStatus

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='materials',
        verbose_name='docente',
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.PROTECT, related_name='materials', verbose_name='materia'
    )
    title = models.CharField('título', max_length=150)
    topic = models.CharField('tema', max_length=150,
                             help_text='Ej.: El ciclo del agua. Da contexto a la IA.')
    input_type = models.CharField('tipo de entrada', max_length=5, choices=InputType.choices)
    file = models.FileField(
        'archivo',
        upload_to=material_path,
        blank=True,
        validators=[FileExtensionValidator(ALLOWED_EXTENSIONS), validate_size],
    )
    file_name = models.CharField('nombre del archivo', max_length=255, blank=True)
    size_kb = models.PositiveIntegerField('tamaño (KB)', null=True, blank=True)
    input_text = models.TextField('texto ingresado', blank=True)
    status = models.CharField('estado', max_length=10, choices=Status.choices, default=Status.PENDING)
    ai_analysis = models.JSONField('análisis de la IA', null=True, blank=True)  # jsonb in PostgreSQL
    ai_error = models.TextField('error de la IA', blank=True)
    created_at = models.DateTimeField('creado', auto_now_add=True)
    updated_at = models.DateTimeField('actualizado', auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'material original'
        verbose_name_plural = 'materiales originales'
        constraints = [
            models.CheckConstraint(
                check=models.Q(status__in=MaterialStatus.values), name='material_status_valid'
            ),
            models.CheckConstraint(
                check=models.Q(input_type__in=InputType.values), name='material_input_type_valid'
            ),
            # Text: the text is required. Photo, PDF or Word: the file is required.
            models.CheckConstraint(
                check=(
                    models.Q(input_type=InputType.TEXT) & ~models.Q(input_text='')
                ) | (
                    ~models.Q(input_type=InputType.TEXT) & ~models.Q(file='')
                ),
                name='material_content_matches_type',
            ),
        ]

    def clean(self):
        if self.input_type in EXTENSIONS_BY_TYPE and self.file:
            extension = Path(self.file.name).suffix.lower().lstrip('.')
            if extension not in EXTENSIONS_BY_TYPE[self.input_type]:
                raise ValidationError({'file': 'El archivo no corresponde al tipo de entrada elegido.'})

    def __str__(self):
        return self.title
