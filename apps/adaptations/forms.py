import re
from pathlib import Path

from django import forms
from django.conf import settings

from apps.core.choices import AdaptationGrade, RecordStatus, UdlAdjustment
from apps.core.forms import AccessibleFormMixin
from apps.materials.models import EXTENSIONS_BY_TYPE, InputType, Subject

from . import document_privacy, lesson_plan

ACCEPT_BY_TYPE = {
    InputType.PHOTO: 'image/jpeg,image/png,image/webp',
    InputType.PDF: 'application/pdf',
    InputType.WORD: '.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document',
}


class AdaptMaterialForm(AccessibleFormMixin, forms.Form):
    """Upload a worksheet and choose the students: each one gets a worksheet for all their conditions."""
    students = forms.MultipleChoiceField(
        label='Estudiantes', widget=forms.CheckboxSelectMultiple,
        error_messages={'required': 'Selecciona al menos un estudiante.'},
    )
    subject = forms.ModelChoiceField(label='Materia', queryset=Subject.objects.filter(record_status=RecordStatus.ACTIVE), empty_label='Selecciona la materia')
    # The title is read from the worksheet itself; the topic is the teacher's, and the AI checks the worksheet
    # works it (and the subject) before adapting
    topic = forms.CharField(label='Tema', max_length=150,
                            help_text='El tema que trabaja la guía. La IA comprueba que el material trate este tema y la materia elegida antes de adaptarlo.',
                            widget=forms.TextInput(attrs={'placeholder': 'Ej.: Fracciones simples y reparto equitativo'}))
    # Photos are not offered: their personal data cannot be checked reliably before sending them to the AI
    input_type = forms.ChoiceField(label='Tipo de entrada', initial=InputType.PDF, widget=forms.RadioSelect,
                                   choices=[c for c in InputType.choices if c[0] != InputType.PHOTO])
    file = forms.FileField(label='Archivo de la guía', required=False)
    input_text = forms.CharField(label='Texto de la guía', required=False,
                                 widget=forms.Textarea(attrs={'rows': 8, 'placeholder': 'Escribe o pega aquí el texto de la guía...'}))

    def __init__(self, *args, teacher, **kwargs):
        super().__init__(*args, **kwargs)
        self.student_objects = {str(s.pk): s for s in teacher.students.filter(record_status=RecordStatus.ACTIVE).order_by('created_at')}
        # Names the privacy check removes from the material (every student of this teacher)
        self.private_names = [s.name for s in teacher.students.all()]
        self.fields['students'].choices = [(pk, s.name) for pk, s in self.student_objects.items()]
        # The UDL rules are chosen per student: each starts with the ones recommended for their own NEE
        for pk, student in self.student_objects.items():
            self.fields[self.rules_field(pk)] = forms.MultipleChoiceField(
                label=f'Reglas para {student.name}', choices=UdlAdjustment.choices, required=False,
                initial=recommended_rules(student), widget=forms.CheckboxSelectMultiple)

    @staticmethod
    def rules_field(pk):
        return f'rules_{pk}'

    def student_rules(self):
        """(student, its rules field, the rules recommended for it) for the template, in the students' order."""
        return [(student, self[self.rules_field(pk)], recommended_rules(student))
                for pk, student in self.student_objects.items()]

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
                else:
                    self.check_privacy('file', file)
        return data

    def check_privacy(self, field, upload):
        """The local privacy check, at upload time: a file that cannot be sent safely is refused here."""
        upload.seek(0)
        inspection = document_privacy.inspect_file(upload.read(), self.private_names)
        upload.seek(0)
        if not inspection.allowed:
            self.add_error(field, inspection.message)
        return inspection

    def requests(self):
        """(student_id, conditions) pairs to adapt: one worksheet per student, with all their conditions."""
        return [(self.student_objects[pk].pk, list(self.student_objects[pk].conditions))
                for pk in self.cleaned_data['students']]

    def chosen_adjustments(self):
        """
        {student id: the UDL rules the teacher left ticked for that student}. With none ticked the student
        still gets the mandatory rules of their NEE (udl_rules.CONDITION_RULES): these are extra adjustments.
        """
        return {self.student_objects[pk].pk: self.cleaned_data.get(self.rules_field(pk), [])
                for pk in self.cleaned_data['students']}


def recommended_rules(student):
    """The UDL rules recommended for all the student's NEE groups, without repeats."""
    rules = []
    for group in student.groups:
        rules += [str(rule) for rule in group.adjustments if str(rule) not in rules]
    return rules


MAX_PLAN_MB = 10
MAX_PAGE_FILES = 10  # a whole week can use several pages of the book
PAGE_TYPES = {b'%PDF-': 'pdf', b'\x89PNG': 'png', b'\xff\xd8\xff': 'jpg', b'RIFF': 'webp'}


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """The pages of the class: one digital PDF (photos are refused by the privacy check of the form)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput(attrs={'accept': 'application/pdf'}))
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
    """Step 1 of "Adaptar clase": the teacher's class plan in Excel, Word or a digital PDF."""
    plan_file = forms.FileField(label='Planificación (Excel, Word o PDF)',
                                widget=forms.ClearableFileInput(attrs={'accept': '.xlsx,.xls,.docx,.pdf'}))

    def clean_plan_file(self):
        upload = self.cleaned_data['plan_file']
        if Path(upload.name).suffix.lower() not in lesson_plan.EXTENSIONS.values():
            raise forms.ValidationError('Sube la planificación en Excel (.xlsx o .xls), Word (.docx) o PDF.')
        if upload.size > MAX_PLAN_MB * 1024 * 1024:
            raise forms.ValidationError(f'El archivo supera los {MAX_PLAN_MB} MB.')
        content = upload.read()
        upload.seek(0)
        try:
            found = lesson_plan.sessions(content)
        except lesson_plan.PlanError as error:
            raise forms.ValidationError(str(error)) from error
        if not found:
            raise forms.ValidationError('No se encontró ninguna clase en el archivo.')
        return upload


class LessonSetupForm(AdaptMaterialForm):
    """Step 2: the session to adapt, its student book pages, the optional teacher guide and the students."""
    session = forms.ChoiceField(label='Semana a adaptar')
    book_files = MultipleFileField(label='Páginas del libro del alumno', required=True)
    guide_files = MultipleFileField(label='Páginas de la guía del docente (opcional)', required=False)
    adaptation_grade = forms.ChoiceField(
        label='Grado de adaptación', required=False,
        choices=[('', 'Que la IA sugiera el grado')] + AdaptationGrade.choices,
    )
    # Only shown after the AI said the pages are of another topic: the teacher can overrule it
    skip_topic_check = forms.BooleanField(label='Las páginas son correctas: adaptar de todas formas',
                                          required=False)

    def __init__(self, *args, sessions, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('topic', 'input_type', 'file', 'input_text'):
            del self.fields[name]  # they come from the chosen session and the book pages
        # One class per week, "Semana 19 · its topic": the week is adapted whole (a week has one topic and
        # its sessions are the class days). Weeks in order; sheets that are not a week (e.g. PCA) last.
        weeks = sorted(lesson_plan.weeks(sessions), key=lambda week: (
            int(week['week'].split()[-1]) if re.fullmatch(r'Semana \d+', week['week']) else 10 ** 6))
        # A sheet that is neither a week nor has sessions (the yearly plan, "PCA") is not a class to adapt
        weeks = [week for week in weeks if week['sessions'] or re.fullmatch(r'Semana \d+', week['week'])] or weeks
        self.sessions = {week['key']: week for week in weeks}
        self.fields['session'].choices = [('', 'Selecciona la semana')] + [
            (week['key'], f"{week['week']} · {week['title']}") for week in weeks]

    def clean(self):
        data = super().clean()
        for field in ('book_files', 'guide_files'):
            uploads = data.get(field) or []
            if not uploads or field in self.errors:
                continue
            if len(uploads) > 1:
                self.add_error(field, 'Sube las páginas en un solo PDF digital: por privacidad, las fotos no se '
                                      'envían a la IA.')
            else:
                self.check_privacy(field, uploads[0])
        return data

    def chosen_session(self):
        return self.sessions[self.cleaned_data['session']]
