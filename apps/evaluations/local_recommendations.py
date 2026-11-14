"""
Task 4.1: recommendations after a classroom evaluation built by fixed rules, in this server, without calling the
AI. They are used whenever the evaluation data may not be sent to an external provider (and next to the AI's
advice when it may). Each one names the indicators it is based on; an indicator that was not registered is
never assumed. They are orientations for the teacher to review: not clinical conclusions, not demonstrated
results, and they never credit a support with a result.
"""
from .models import (
    SUPPORT_CHOICES, AchievementLevel, AutonomyLevel, Comprehension, GoalAttainment, Overload,
)

LIMITATION = ('Orientaciones generadas con reglas fijas a partir de los indicadores registrados en esta evaluación. '
              'No son conclusiones clínicas ni resultados demostrados: la decisión es del docente.')
INSUFFICIENT = 'No existen indicadores suficientes para formular una recomendación específica.'
LOW_GOAL = {GoalAttainment.MUCH_LESS, GoalAttainment.LESS}
HIGH_GOAL = {GoalAttainment.MORE, GoalAttainment.MUCH_MORE}
HARD_COMPREHENSION = {Comprehension.DIFFICULTY, Comprehension.NO}
HIGH_SUPPORT = {AutonomyLevel.FREQUENT_SUPPORT, AutonomyLevel.CONSTANT_SUPPORT}
LOW_SUPPORT = {AutonomyLevel.AUTONOMOUS, AutonomyLevel.OCCASIONAL_SUPPORT}
OVERLOAD_SIGNS = {Overload.MILD, Overload.EVIDENT}
SUPPORT_LABELS = dict(SUPPORT_CHOICES)


def _basis(evaluation, *names):
    """The registered answers a recommendation rests on, as the teacher saw them."""
    labels = {'goal_attainment': 'Logro', 'comprehension': 'Comprensión', 'autonomy_level': 'Nivel de apoyo',
              'overload': 'Sobrecarga', 'achievement_level': 'Escala del Ministerio'}
    return '; '.join(f'{labels[name]}: {getattr(evaluation, f"get_{name}_display")()}'
                     for name in names if getattr(evaluation, name))


def recommend(evaluation, objective=''):
    """[{'text', 'basis'}] for this evaluation. With nothing specific to say, a single 'insufficient' item."""
    items = []
    goal, comprehension = evaluation.goal_attainment, evaluation.comprehension
    if goal in LOW_GOAL or comprehension in HARD_COMPREHENSION or evaluation.achievement_level == AchievementLevel.NOT_YET:
        text = 'Conviene revisar la claridad de las instrucciones y comprobar la comprensión antes de continuar'
        items.append({'text': text + (f' con el objetivo «{objective.rstrip(". ")}».' if objective else '.'),
                      'basis': _basis(evaluation, 'goal_attainment', 'comprehension', 'achievement_level')})
    if evaluation.overload in OVERLOAD_SIGNS:
        items.append({'text': 'El docente puede valorar una organización de la actividad en segmentos más breves.',
                      'basis': _basis(evaluation, 'overload')})
    if evaluation.autonomy_level in HIGH_SUPPORT:
        items.append({'text': 'Se registró un nivel de apoyo alto: conviene identificar en qué pasos de la actividad '
                              'se necesitó más acompañamiento.',
                      'basis': _basis(evaluation, 'autonomy_level')})
    if goal in HIGH_GOAL and comprehension == Comprehension.YES and evaluation.autonomy_level in LOW_SUPPORT:
        items.append({'text': 'El objetivo adaptado se alcanzó o superó con poco apoyo: el docente puede valorar si la '
                              'próxima actividad similar mantiene o reduce las adaptaciones.',
                      'basis': _basis(evaluation, 'goal_attainment', 'comprehension', 'autonomy_level')})
    for key in evaluation.supports_useful:
        if key in SUPPORT_LABELS:
            items.append({'text': f'Se registró «{SUPPORT_LABELS[key]}» como útil durante la actividad. Conviene valorar '
                                  'su pertinencia en futuras actividades similares.',
                          'basis': 'Apoyo aplicado y marcado como útil por el docente'})
    for key in evaluation.supports_applied:
        if key in SUPPORT_LABELS and key not in evaluation.supports_useful:
            items.append({'text': f'Se aplicó «{SUPPORT_LABELS[key]}» sin marcarlo como útil: el docente puede valorar si '
                                  'conviene mantenerlo.',
                          'basis': 'Apoyo aplicado, no marcado como útil'})
    return items or [{'text': INSUFFICIENT, 'basis': _basis(evaluation, 'goal_attainment', 'comprehension',
                                                             'autonomy_level', 'overload') or 'Sin indicadores registrados'}]
