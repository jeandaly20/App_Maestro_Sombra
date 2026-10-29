from pathlib import Path

from django import forms
from django.conf import settings

from apps.core.choices import AdaptationGrade, Condition, RecordStatus, UdlAdjustment
from apps.core.forms import AccessibleFormMixin
from apps.materials.models import EXTENSIONS_BY_TYPE, InputType, Subject

from . import lesson_plan

ACCEPT_BY_TYPE = {
    InputType.PHOTO: 'image/jpeg,image/png,image/webp',
    InputType.PDF: 'application/pdf',
    InputType.WORD: '.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document',
}


class AdaptMaterialForm(AccessibleFormMixin, forms.Form):
    """Upload a worksheet and choose which students (and which of their conditions) to adapt it for."""
    students = forms.MultipleChoiceField(
        label='Estudiantes', widget=forms.CheckboxSelectMultiple,
        error_messages={'required': 'Selecciona al menos un estudiante.'},
    )
    subject = forms.ModelChoiceField(label='Materia', queryset=Subject.objects.filter(record_status=RecordStatus.ACTIVE), empty_label='Selecciona la materia')
    title = forms.CharField(label='Título de la guía', max_length=150,
                            widget=forms.TextInput(attrs={'placeholder': 'Ej.: Fracciones y repartos simples'}))
    topic = forms.CharField(label='Tema', max_length=150,
                            widget=forms.TextInput(attrs={'placeholder': 'Ej.: Fracciones simples y reparto equitativo'}))
    input_type = forms.ChoiceField(label='Tipo de entrada', choices=InputType.choices, initial=InputType.PHOTO,
                                   widget=forms.RadioSelect)
    file = forms.FileField(label='Archivo de la guía', required=False)
    input_text = forms.CharField(label='Texto de la guía', required=False,
                                 widget=forms.Textarea(attrs={'rows': 8, 'placeholder': 'Escribe o pega aquí el texto de la guía...'}))
    adjustments = forms.MultipleChoiceField(label='Reglas de adaptación aplicadas', choices=UdlAdjustment.choices,
                                            required=False, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, teacher, **kwargs):
        super().__init__(*args, **kwargs)
        self.student_objects = {str(s.pk): s for s in teacher.students.filter(record_status=RecordStatus.ACTIVE).order_by('created_at')}
        self.fields['students'].choices = [(pk, s.name) for pk, s in self.student_objects.items()]
        # A student with several conditions: the teacher picks which one this worksheet adapts for
        for pk, student in self.student_objects.items():
            if len(student.conditions) > 1:
                self.fields[f'condition_{pk}'] = forms.ChoiceField(
                    label=f'Condición a adaptar para {student.name}',
                    choices=[(c, Condition(c).label) for c in student.conditions], required=False,
                )

    def condition_fields(self):
        return {pk: self[f'condition_{pk}'] for pk in self.student_objects if f'condition_{pk}' in self.fields}

    def clean(self):
        data = super().clean()
        input_type = data.get('input_type')
        file = data.get('file')
        if input_type == InputType.TEXT:
            if not (data.get('input_text') or '').strip():
                self.add_error('input_text', 'Escribe o pega el texto de la guía.')
        elif input_type:
            if not file:
                self.add_error('file', 'Sube el archivo de la guía.')
            else:
                extension = Path(file.name).suffix.lower().lstrip('.')
                if extension not in EXTENSIONS_BY_TYPE[input_type]:
                    allowed = ', '.join(EXTENSIONS_BY_TYPE[input_type]).upper()
                    self.add_error('file', f'Este tipo de entrada acepta archivos {allowed}.')
                elif file.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
                    self.add_error('file', f'El archivo supera los {settings.MAX_UPLOAD_MB} MB.')
        return data

    def requests(self):
        """(student_id, condition) pairs to adapt, one per selected student."""
        pairs = []
        for pk in self.cleaned_data['students']:
            student = self.student_objects[pk]
            chosen = self.cleaned_data.get(f'condition_{pk}') or student.conditions[0]
            pairs.append((student.pk, chosen))
        return pairs

    def chosen_adjustments(self):
        """The teacher's choice, or the recommended adjustments of the selected students."""
        if self.cleaned_data['adjustments']:
            return self.cleaned_data['adjustments']
        recommended = []
        for pk in self.cleaned_data['students']:
            for group in self.student_objects[pk].groups:
                recommended += [a for a in group.adjustments if a not in recommended]
        return [str(a) for a in recommended]


MAX_PLAN_MB = 10
MAX_PAGE_FILES = 6
PAGE_TYPES = {b'%PDF-': 'pdf', b'\x89PNG': 'png', b'\xff\xd8\xff': 'jpg', b'RIFF': 'webp'}


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Several photos of the same pages (or one PDF)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput(attrs={'accept': 'image/jpeg,image/png,image/webp,application/pdf'}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        cleaned = [super(MultipleFileField, self).clean(item, initial) for item in files]
        if self.required and not cleaned:
            raise forms.ValidationError(self.error_messages['required'], code='required')
        if len(cleaned) > MAX_PAGE_FILES:
            raise forms.ValidationError(f'Sube como máximo {MAX_PAGE_FILES} fotos o un PDF.')
        kinds = []
        for item in cleaned:
            if item.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
                raise forms.ValidationError(f'«{item.name}» supera los {settings.MAX_UPLOAD_MB} MB.')
            head = item.read(8)
            item.seek(0)
            kind = next((value for signature, value in PAGE_TYPES.items() if head.startswith(signature)), None)
            if not kind:
                raise forms.ValidationError(f'«{item.name}» no es una foto (JPG, PNG o WEBP) ni un PDF.')
            kinds.append(kind)
        if 'pdf' in kinds and len(cleaned) > 1:
            raise forms.ValidationError('Sube un solo PDF, o varias fotos.')
        return cleaned


class LessonUploadForm(AccessibleFormMixin, forms.Form):
    """Step 1 of "Adaptar clase": the teacher's class plan in Excel."""
    plan_file = forms.FileField(label='Planificación en Excel (.xlsx)',
                                widget=forms.ClearableFileInput(attrs={'accept': '.xlsx'}))

    def clean_plan_file(self):
        upload = self.cleaned_data['plan_file']
        if Path(upload.name).suffix.lower() != '.xlsx':
            raise forms.ValidationError('Sube la planificación en formato Excel (.xlsx).')
        if upload.size > MAX_PLAN_MB * 1024 * 1024:
            raise forms.ValidationError(f'El archivo supera los {MAX_PLAN_MB} MB.')
        content = upload.read()
        upload.seek(0)
        try:
            found = lesson_plan.sessions(content)
        except Exception as error:  # noqa: BLE001 - a damaged or non-Excel file
            raise forms.ValidationError('No se pudo leer el archivo. Verifica que sea un Excel (.xlsx) válido.') from error
        if not found:
            raise forms.ValidationError('No se encontró ninguna clase en el archivo.')
        return upload


class LessonSetupForm(AdaptMaterialForm):
    """Step 2: the session to adapt, its student book pages, the optional teacher guide and the students."""
    session = forms.ChoiceField(label='Clase (sesión) a adaptar')
    book_files = MultipleFileField(label='Páginas del libro del alumno', required=True)
    guide_files = MultipleFileField(label='Páginas de la guía del docente (opcional)', required=False)
    adaptation_grade = forms.ChoiceField(
        label='Grado de adaptación', required=False,
        choices=[('', 'Que la IA sugiera el grado')] + AdaptationGrade.choices,
    )

    def __init__(self, *args, sessions, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('title', 'topic', 'input_type', 'file', 'input_text'):
            del self.fields[name]  # they come from the chosen session and the book pages
        self.sessions = {s['key']: s for s in sessions}
        self.fields['session'].choices = [('', 'Selecciona la clase')] + [
            (key, f"{s['sheet'].strip()} · {s['label']}{' · ' + s['title'] if s['title'] else ''}")
            for key, s in self.sessions.items()
        ]

    def chosen_session(self):
        return self.sessions[self.cleaned_data['session']]
