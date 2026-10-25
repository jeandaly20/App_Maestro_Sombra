from django import forms

from apps.core.forms import AccessibleFormMixin

from .models import Evaluation


# (field, hint, required to finish, icon per option)
QUESTIONS = [
    ('autonomy_level', 'Necesidad de apoyo directo durante la sesión.', True,
     {'AUTONOMOUS': 'sentiment_satisfied', 'OCCASIONAL_SUPPORT': 'support', 'CONSTANT_SUPPORT': 'front_hand'}),
    ('comprehension', 'Claridad del lenguaje adaptado a su condición y nivel lector.', True,
     {'YES': 'thumb_up', 'DIFFICULTY': 'info', 'NO': 'cancel'}),
    ('overload', 'Cansancio, frustración o pérdida de atención.', False,
     {'NONE': 'sentiment_satisfied', 'MILD': 'visibility_off', 'EVIDENT': 'sentiment_dissatisfied'}),
    ('accuracy_range', 'Ejercicios resueltos correctamente.', False,
     {'BELOW_50': 'trending_down', 'BETWEEN_50_75': 'trending_flat', 'ABOVE_75': 'trending_up'}),
]


class EvaluationForm(AccessibleFormMixin, forms.ModelForm):
    """
    Post-class questionnaire. A draft may be left incomplete; to finish it the
    teacher must answer at least autonomy and comprehension (same rule as the database).
    """

    class Meta:
        model = Evaluation
        fields = ['autonomy_level', 'comprehension', 'overload', 'accuracy_range', 'observation',
                  'regeneration_requested']
        labels = {
            'autonomy_level': '¿Con qué nivel de autonomía resolvió la actividad?',
            'comprehension': '¿Comprendió la instrucción y el vocabulario?',
            'overload': '¿Mostró signos de sobrecarga o fatiga?',
            'accuracy_range': '¿Qué porcentaje completó correctamente?',
            'observation': 'Observación pedagógica',
            'regeneration_requested': 'Regenerar este material con mayor simplificación',
        }
        help_texts = {'observation': ''}
        widgets = {
            'autonomy_level': forms.RadioSelect,
            'comprehension': forms.RadioSelect,
            'overload': forms.RadioSelect,
            'accuracy_range': forms.RadioSelect,
            'observation': forms.Textarea(attrs={
                'rows': 4, 'maxlength': 1000,
                'placeholder': 'Ej.: Identificó bien 1/2 con apoyo de colores, pero con 1/4 y 1/8 se confundió...',
            }),
        }

    def __init__(self, *args, finishing=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.finishing = finishing
        for name in ('autonomy_level', 'comprehension', 'overload', 'accuracy_range'):
            # Radio cards: no empty "---------" option
            self.fields[name].choices = [c for c in self.fields[name].choices if c[0]]

    def questions(self):
        """The four option-card questions, ready for the template."""
        return [
            {'number': number, 'field': self[name], 'hint': hint, 'required': required,
             'options': [(option, icons[option.data['value']]) for option in self[name]]}
            for number, (name, hint, required, icons) in enumerate(QUESTIONS, start=1)
        ]

    def clean(self):
        data = super().clean()
        if self.finishing:
            for name in ('autonomy_level', 'comprehension'):
                if not data.get(name):
                    self.add_error(name, 'Responde esta pregunta para finalizar la evaluación.')
        return data
