from datetime import date

from django import forms

from apps.core.choices import Condition, Grade
from apps.core.forms import AccessibleFormMixin

from .models import Student


class StudentForm(AccessibleFormMixin, forms.ModelForm):
    conditions = forms.MultipleChoiceField(
        label='Condiciones NEE',
        choices=Condition.choices,
        widget=forms.CheckboxSelectMultiple,
        error_messages={'required': 'Selecciona al menos una condición.'},
    )

    class Meta:
        model = Student
        fields = ['name', 'birth_date', 'grade', 'conditions', 'notes']
        labels = {
            'name': 'Nombre del niño o niña',
            'birth_date': 'Fecha de nacimiento',
            'grade': 'Grado',
            'notes': 'Notas de aula y estilo de aprendizaje',
        }
        help_texts = {'name': '', 'notes': ''}
        widgets = {
            'name': forms.TextInput(attrs={'autocomplete': 'off', 'placeholder': 'Ej.: Mateo Benítez'}),
            'birth_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': 'Fortalezas observadas, apoyos sensoriales, estilo de aprendizaje...',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['birth_date'].widget.attrs['max'] = date.today().isoformat()
        self.fields['grade'].choices = [('', 'Selecciona el grado')] + Grade.choices

    def clean_conditions(self):
        # Always stored in catalog order, whatever order the boxes were ticked
        chosen = set(self.cleaned_data['conditions'])
        return [value for value in Condition.values if value in chosen]

    def clean_name(self):
        name = ' '.join(self.cleaned_data['name'].split())
        if len(name) < 3:
            raise forms.ValidationError('Escribe el nombre del estudiante.')
        return name
