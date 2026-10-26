from datetime import date

from django import forms

from apps.core.choices import Condition, DiagnosisSource, Grade
from apps.core.forms import AccessibleFormMixin

from .documents import ACCEPT, MAX_DOCUMENT_MB, detect_type
from .models import Student


class StudentForm(AccessibleFormMixin, forms.ModelForm):
    diagnosis_document = forms.FileField(
        label='Documento de respaldo (opcional)', required=False,
        widget=forms.ClearableFileInput(attrs={'accept': ACCEPT}),
    )
    diagnosis_consent = forms.BooleanField(
        label='El representante legal del estudiante autorizó guardar este documento en el sistema.', required=False,
    )
    remove_document = forms.BooleanField(label='Quitar el documento guardado', required=False)
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
        self.fields['birth_date'].widget.attrs['max'] = date.today().isoformat()
        self.fields['diagnosis_date'].widget.attrs['max'] = date.today().isoformat()
        # The teacher picks the document consciously: no option is preselected for a new student
        self.fields['diagnosis_source'].choices = [('', 'Selecciona el documento')] + DiagnosisSource.choices
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
        if data.get('diagnosis_document'):
            if not data.get('diagnosis_consent'):
                self.add_error('diagnosis_consent', 'Para guardar el documento necesitas la autorización del representante.')
            if data.get('diagnosis_source') == DiagnosisSource.IN_EVALUATION:
                self.add_error('diagnosis_document', 'Si subes un documento, indica cuál es en lugar de "en proceso de evaluación".')
        elif data.get('diagnosis_source') == DiagnosisSource.IN_EVALUATION and self.instance.has_document:
            # Back to "in evaluation": the stored document no longer applies
            data['remove_document'] = True
        # Tells the model whether a document backs the diagnosis after saving (then the date is optional)
        keeps_stored = self.instance.has_document and not data.get('remove_document')
        self.instance.document_attached = bool(data.get('diagnosis_document')) or keeps_stored
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
