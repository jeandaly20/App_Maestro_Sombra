from datetime import date, timedelta

from django import forms
from django.conf import settings

from apps.core.choices import Condition, DiagnosisSource, Grade
from apps.core.forms import AccessibleFormMixin

from .documents import ACCEPT, MAX_DOCUMENT_MB, detect_type
from .models import MAX_AGE, MIN_AGE, Student


def years_ago(years, today=None):
    today = today or date.today()
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # 29 February in a non-leap year
        return today.replace(year=today.year - years, day=28)


class StudentForm(AccessibleFormMixin, forms.ModelForm):
    diagnosis_document = forms.FileField(
        label='Documento de respaldo', required=False,  # required in clean(): an edit may keep the stored one
        widget=forms.ClearableFileInput(attrs={'accept': ACCEPT}),
    )
    diagnosis_consent = forms.BooleanField(
        label='El representante legal del estudiante autorizó guardar este documento en el sistema.', required=False,
    )
    conditions = forms.MultipleChoiceField(
        label='Condiciones NEE',
        choices=Condition.choices,
        widget=forms.CheckboxSelectMultiple,
        error_messages={'required': 'Selecciona al menos una condición.'},
    )

    class Meta:
        model = Student
        fields = ['name', 'birth_date', 'grade', 'conditions', 'diagnosis_source', 'diagnosis_date', 'notes']
        labels = {
            'name': 'Nombre del niño o niña',
            'birth_date': 'Fecha de nacimiento',
            'grade': 'Grado',
            'notes': 'Notas de aula y estilo de aprendizaje',
            'diagnosis_source': 'Documento que respalda el diagnóstico',
            'diagnosis_date': 'Fecha del informe o certificado',
        }
        help_texts = {'name': '', 'notes': ''}
        widgets = {
            'name': forms.TextInput(attrs={'autocomplete': 'off', 'placeholder': 'Ej.: Mateo Benítez'}),
            'birth_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'diagnosis_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': 'Fortalezas observadas, apoyos sensoriales, estilo de aprendizaje...',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Oldest allowed: turns MAX_AGE + 1 tomorrow; youngest: turned MIN_AGE today
        oldest = years_ago(MAX_AGE + 1) + timedelta(days=1)
        self.fields['birth_date'].widget.attrs.update({
            'min': oldest.isoformat(), 'max': years_ago(MIN_AGE).isoformat(),
            'data-min-age': MIN_AGE, 'data-max-age': MAX_AGE,
        })
        self.fields['diagnosis_date'].widget.attrs['max'] = date.today().isoformat()
        # The teacher picks the document consciously: no option is preselected for a new student.
        # When the document is required, "in evaluation" is not offered (an enrolled student has the report).
        self.document_required = settings.DIAGNOSIS_DOCUMENT_REQUIRED
        self.fields['diagnosis_source'].choices = [('', 'Selecciona el documento')] + [
            choice for choice in DiagnosisSource.choices
            if not (self.document_required and choice[0] == DiagnosisSource.IN_EVALUATION)
        ]
        if self.instance._state.adding:
            self.initial['diagnosis_source'] = ''
        self.fields['grade'].choices = [('', 'Selecciona el grado')] + Grade.choices

    def clean_diagnosis_document(self):
        uploaded = self.cleaned_data.get('diagnosis_document')
        if not uploaded:
            return None
        if uploaded.size > MAX_DOCUMENT_MB * 1024 * 1024:
            raise forms.ValidationError(f'El documento supera los {MAX_DOCUMENT_MB} MB.')
        self.document_type = detect_type(uploaded.read(8))
        uploaded.seek(0)
        if not self.document_type:
            raise forms.ValidationError('Sube el documento en PDF, JPG o PNG.')
        return uploaded

    def clean(self):
        data = super().clean()
        uploaded = data.get('diagnosis_document')
        if uploaded and not data.get('diagnosis_consent'):
            self.add_error('diagnosis_consent', 'Para guardar el documento necesitas la autorización del representante.')
        in_evaluation = data.get('diagnosis_source') == DiagnosisSource.IN_EVALUATION
        if uploaded and in_evaluation:
            self.add_error('diagnosis_document', 'Si subes un documento, indica cuál es en lugar de "en proceso de evaluación".')
        if (self.document_required and not uploaded and not self.instance.has_document
                and 'diagnosis_document' not in self.errors):
            self.add_error('diagnosis_document', (
                'Adjunta el informe o certificado que respalda el diagnóstico (UDAI, MSP, DECE o profesional clínico).'
            ))
        # Tells the model whether a document backs the diagnosis after saving (then the date is optional)
        self.instance.document_attached = bool(uploaded) or (self.instance.has_document and not in_evaluation)
        self.remove_document = in_evaluation and self.instance.has_document
        return data

    def clean_conditions(self):
        # Always stored in catalog order, whatever order the boxes were ticked
        chosen = set(self.cleaned_data['conditions'])
        return [value for value in Condition.values if value in chosen]

    def clean_name(self):
        name = ' '.join(self.cleaned_data['name'].split())
        if len(name) < 3:
            raise forms.ValidationError('Escribe el nombre del estudiante.')
        return name
