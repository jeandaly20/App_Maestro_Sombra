from datetime import date, timedelta
from types import SimpleNamespace

from django import forms
from django.conf import settings

from apps.core.choices import Condition, DiagnosisSource, Grade
from apps.core.forms import AccessibleFormMixin

from . import documents
from .consent import check_signed
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
    consent_document = forms.FileField(
        label='Autorización firmada por el representante', required=False,  # required in clean() with a document
        widget=forms.ClearableFileInput(attrs={'accept': ACCEPT}),
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
        self.document_check = None  # the reading of the document, done once here and stored by the view

    @staticmethod
    def _checked_file(uploaded, what):
        """(file, real type) of an uploaded PDF, JPG or PNG; the type comes from its first bytes."""
        if not uploaded:
            return None, None
        if uploaded.size > MAX_DOCUMENT_MB * 1024 * 1024:
            raise forms.ValidationError(f'{what} supera los {MAX_DOCUMENT_MB} MB.')
        kind = detect_type(uploaded.read(8))
        uploaded.seek(0)
        if not kind:
            raise forms.ValidationError(f'Sube {what.lower()} en PDF, JPG o PNG.')
        return uploaded, kind

    def clean_diagnosis_document(self):
        uploaded, self.document_type = self._checked_file(self.cleaned_data.get('diagnosis_document'), 'El documento')
        return uploaded

    def clean_consent_document(self):
        uploaded, self.consent_type = self._checked_file(self.cleaned_data.get('consent_document'), 'La autorización')
        return uploaded

    def clean(self):
        data = super().clean()
        uploaded = data.get('diagnosis_document')
        consent = data.get('consent_document')
        # The document is stored only with the representative's signed authorization (LOPDP)
        if uploaded and not consent and not self.instance.has_signed_consent and 'consent_document' not in self.errors:
            self.add_error('consent_document', 'Sube la autorización firmada por el representante para guardar el '
                                               'documento. Descarga el formato, que lo firme y sube una foto o un escaneo.')
        if consent and not uploaded and not self.instance.has_document:
            self.add_error('consent_document', 'La autorización acompaña al documento del diagnóstico: adjunta también '
                                               'el documento.')
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
        if not in_evaluation:
            self.check_backing(data, uploaded, consent)
        return data

    def check_backing(self, data, uploaded, consent):
        """
        The registration is not saved unless the document is this student's (name, chosen institution and one of
        the NEE) and the authorization is the app's form for this student, signed and with the representative's
        name. Both are read here, on the server, and never sent to the AI.
        """
        if not (data.get('name') and data.get('conditions') and data.get('diagnosis_source') and data.get('grade')):
            return  # their own errors are shown first
        student = SimpleNamespace(name=data['name'], diagnosis_source=data['diagnosis_source'],
                                  conditions=data['conditions'])
        if uploaded and 'diagnosis_document' not in self.errors:
            uploaded.seek(0)
            self.document_check = documents.check_document(student, uploaded.read(), self.document_type)
            uploaded.seek(0)
            error = documents.document_error(self.document_check, data['name'])
            if error:
                self.add_error('diagnosis_document', error)
        elif not uploaded and self.instance.has_document:
            check = self.instance.diagnosis_check or {}
            if set(self.changed_data) & {'name', 'diagnosis_source', 'conditions'} or not check:
                check = self.document_check = documents.check_document(
                    student, documents.read_document(self.instance), self.instance.diagnosis_file_type)
            error = documents.document_error(check, data['name'])
            if error:
                error = error.replace('El documento no coincide con el estudiante', 'El documento guardado no '
                                      'coincide con los datos de la ficha')
                self.add_error('diagnosis_document', f'{error} Corrige los datos o sube el documento correcto.')
        if consent and 'consent_document' not in self.errors:
            consent.seek(0)
            error = check_signed(consent.read(), self.consent_type, student_name=data['name'],
                                 grade_label=Grade(data['grade']).label)
            consent.seek(0)
            if error:
                self.add_error('consent_document', error)

    def clean_conditions(self):
        # Always stored in catalog order, whatever order the boxes were ticked
        chosen = set(self.cleaned_data['conditions'])
        return [value for value in Condition.values if value in chosen]

    def clean_name(self):
        name = ' '.join(self.cleaned_data['name'].split())
        if len(name) < 3:
            raise forms.ValidationError('Escribe el nombre del estudiante.')
        return name
