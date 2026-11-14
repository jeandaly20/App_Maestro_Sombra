from django import forms

from apps.core.forms import AccessibleFormMixin

from .models import SUPPORT_CHOICES, Evaluation

# The five research-backed questions (see models.py): (field, hint, required to finish, icon per option)
QUESTIONS = [
    ('goal_attainment', 'Escala de logro de metas individuales (−2 a +2): compara lo que logró con el objetivo '
     'adaptado para él o ella.', True,
     {'MUCH_LESS': 'keyboard_double_arrow_down', 'LESS': 'keyboard_arrow_down', 'EXPECTED': 'check_circle',
      'MORE': 'keyboard_arrow_up', 'MUCH_MORE': 'keyboard_double_arrow_up'}),
    ('autonomy_level', 'Intensidad del apoyo directo que necesitó durante la clase.', True,
     {'AUTONOMOUS': 'sentiment_satisfied', 'OCCASIONAL_SUPPORT': 'support', 'FREQUENT_SUPPORT': 'pan_tool_alt',
      'CONSTANT_SUPPORT': 'front_hand'}),
    ('comprehension', 'Si las instrucciones, el vocabulario y los apoyos de la ficha le resultaron claros (DUA: '
     'formas de representación).', True,
     {'YES': 'thumb_up', 'DIFFICULTY': 'info', 'NO': 'cancel'}),
    ('overload', 'Cansancio, frustración, bloqueo o pérdida de atención durante la actividad.', False,
     {'NONE': 'sentiment_satisfied', 'MILD': 'visibility_off', 'EVIDENT': 'sentiment_dissatisfied'}),
    ('achievement_level', 'Escala cualitativa del Ministerio de Educación, valorada con los indicadores adaptados.',
     False,
     {'MASTERS': 'workspace_premium', 'ACHIEVES': 'trending_up', 'NEAR': 'trending_flat', 'NOT_YET': 'trending_down'}),
]
RADIO_FIELDS = [name for name, *_ in QUESTIONS]
REQUIRED_TO_FINISH = [name for name, _hint, required, _icons in QUESTIONS if required]


class EvaluationForm(AccessibleFormMixin, forms.ModelForm):
    """
    Post-class questionnaire. A draft may be left incomplete; to finish it the teacher must answer the
    goal attainment, the support level and the comprehension (the database requires the last two: the
    evaluations saved before the goal question existed are still valid).
    """

    # Optional: what the teacher really did in class (never filled from the suggestions or the worksheet's rules)
    supports_applied = forms.MultipleChoiceField(
        label='Apoyos pedagógicos utilizados durante la actividad', choices=SUPPORT_CHOICES, required=False,
        widget=forms.CheckboxSelectMultiple)
    supports_useful = forms.MultipleChoiceField(
        label='De los apoyos aplicados, ¿cuáles te parecieron útiles?', choices=SUPPORT_CHOICES, required=False,
        widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = Evaluation
        fields = RADIO_FIELDS + ['observation', 'supports_applied', 'supports_useful', 'supports_note',
                                 'regeneration_requested']
        labels = {
            'goal_attainment': '¿Alcanzó el objetivo adaptado de la clase?',
            'autonomy_level': '¿Qué nivel de apoyo necesitó?',
            'comprehension': '¿Comprendió las instrucciones y el material adaptado?',
            'overload': '¿Mostró signos de sobrecarga cognitiva o fatiga?',
            'achievement_level': '¿Qué nivel de logro alcanzó?',
            'observation': 'Observación pedagógica',
            'supports_note': 'Descripción de los apoyos (opcional)',
            'regeneration_requested': 'Regenerar este material con mayor simplificación',
        }
        help_texts = {'observation': '', 'supports_note': ''}
        widgets = {
            **{name: forms.RadioSelect for name in RADIO_FIELDS},
            'observation': forms.Textarea(attrs={
                'rows': 4, 'maxlength': 1000,
                'placeholder': 'Ej.: Identificó bien 1/2 con apoyo de colores, pero con 1/4 y 1/8 se confundió...',
            }),
            'supports_note': forms.Textarea(attrs={
                'rows': 2, 'maxlength': 500,
                'placeholder': 'Ej.: Se leyó la consigna en voz alta y se hizo una pausa a mitad de la actividad.',
            }),
        }

    def __init__(self, *args, finishing=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.finishing = finishing
        for name in RADIO_FIELDS:
            # Radio cards: no empty "---------" option
            self.fields[name].choices = [c for c in self.fields[name].choices if c[0]]

    def objective(self):
        """The objective the goal question is about: the adapted one of its class, or the worksheet's goal."""
        adaptation = self.instance.adaptation
        lesson = next((item for item in adaptation.lesson_adaptations.all() if item.content.get('adapted_objective')),
                      None)
        return lesson.content['adapted_objective'] if lesson else adaptation.content.get('learning_goal', '')

    def questions(self):
        """The five option-card questions, ready for the template."""
        objective = self.objective()
        return [
            {'number': number, 'field': self[name], 'required': required,
             'hint': f'{hint} Objetivo: «{objective}»' if name == 'goal_attainment' and objective else hint,
             'options': [(option, icons[option.data['value']]) for option in self[name]]}
            for number, (name, hint, required, icons) in enumerate(QUESTIONS, start=1)
        ]

    def clean(self):
        data = super().clean()
        applied = set(data.get('supports_applied') or [])
        if set(data.get('supports_useful') or []) - applied:
            self.add_error('supports_useful', 'Marca como útil solo un apoyo que se aplicó durante la actividad.')
        if self.finishing:
            for name in REQUIRED_TO_FINISH:
                if not data.get(name):
                    self.add_error(name, 'Responde esta pregunta para finalizar la evaluación.')
        return data
