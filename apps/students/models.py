import uuid
from datetime import date

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.choices import Condition, Grade
from apps.core.fields import EncryptedTextField

MAX_STUDENTS_PER_TEACHER = 3


class Student(models.Model):
    """
    Student with special educational needs (SEN). Identified by a pseudonym;
    the real name and notes are stored encrypted and never sent to the AI.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='students',
        verbose_name='docente',
    )
    pseudonym = models.CharField('seudónimo', max_length=20, unique=True, editable=False)
    name = EncryptedTextField('nombre', help_text='Se guarda cifrado.')
    birth_date = models.DateField('fecha de nacimiento')
    grade = models.CharField('grado', max_length=10, choices=Grade.choices)
    conditions = ArrayField(
        models.CharField(max_length=20, choices=Condition.choices),
        verbose_name='condiciones NEE',
        help_text='Una o varias condiciones (p. ej. dislexia y discalculia).',
    )
    notes = EncryptedTextField('observaciones', blank=True, help_text='Se guardan cifradas.')
    created_at = models.DateTimeField('creado', auto_now_add=True)
    updated_at = models.DateTimeField('actualizado', auto_now=True)

    class Meta:
        ordering = ['grade', 'pseudonym']
        verbose_name = 'estudiante'
        verbose_name_plural = 'estudiantes'
        constraints = [
            # The database rejects unknown conditions even if they bypass Django
            models.CheckConstraint(
                check=models.Q(conditions__contained_by=Condition.values),
                name='student_conditions_valid',
            ),
            models.CheckConstraint(
                check=models.Q(conditions__len__gte=1), name='student_has_condition'
            ),
            models.CheckConstraint(check=models.Q(grade__in=Grade.values), name='student_grade_valid'),
        ]
        # The 3-students-per-teacher limit is also enforced by a PostgreSQL trigger

    @property
    def age(self):
        today = date.today()
        born = self.birth_date
        return today.year - born.year - ((today.month, today.day) < (born.month, born.day))

    def clean(self):
        if self.birth_date:
            if self.birth_date > date.today():
                raise ValidationError({'birth_date': 'La fecha no puede ser futura.'})
            if not 3 <= self.age <= 18:
                raise ValidationError({'birth_date': 'La edad debe estar entre 3 y 18 años.'})
        if self.teacher_id and self._state.adding:
            registered = Student.objects.filter(teacher_id=self.teacher_id).count()
            if registered >= MAX_STUDENTS_PER_TEACHER:
                raise ValidationError(
                    f'Un docente puede registrar como máximo {MAX_STUDENTS_PER_TEACHER} estudiantes.'
                )

    def save(self, *args, **kwargs):
        if not self.pseudonym:
            self.pseudonym = f'EST-{self.id.hex[:6].upper()}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.pseudonym
