"""
UDL (DUA) adaptation rules per SEN condition.

These rules turn the UDL guidelines (CAST, 2024) and easy-to-read practice into
explicit instructions that the AI must follow for every worksheet. They are sent
to the model as text; visual format (fonts, spacing, colors) is NOT left to the
model: the system templates apply it (see templates/adaptations/).
The rule texts are in Spanish because the model must write the worksheet in Spanish.
"""
from apps.core.choices import Condition, UdlAdjustment

from .curriculum import subject_and_level_rules

CONDITION_RULES = {
    Condition.DYSLEXIA: [
        'Usa oraciones cortas, de una sola idea, y vocabulario frecuente.',
        'Divide cada consigna en pasos numerados; nunca un párrafo largo.',
        'Evita palabras con letras que se confunden fácilmente cuando exista un sinónimo sencillo.',
        'Resalta la palabra clave de cada consigna indicándola en el campo "keyword".',
        'No pidas copiar textos largos ni redactar párrafos extensos.',
    ],
    Condition.DYSCALCULIA: [
        'Estas reglas se aplican SOLO a los números y cálculos que ya tiene la guía. Si la guía no tiene '
        'números ni cálculos (por ejemplo, una lectura), no agregues ninguno: aplica solo instrucciones claras y cortas.',
        'Presenta un solo cálculo por paso y explica qué operación hacer con palabras.',
        'Ancla cada número a un objeto concreto (porciones, bloques, objetos del aula).',
        'Describe un apoyo visual para cada cantidad (barras, círculos divididos, recta numérica).',
        'Escribe los números en cifras, nunca en letras, y mantén las unidades visibles.',
        'Evita enunciados con datos que no se usan.',
    ],
    Condition.ADHD: [
        'Divide la tarea en bloques cortos con una meta clara por paso.',
        'Indica al inicio cuántos pasos tiene la actividad para que el estudiante vea el final.',
        'Elimina información decorativa o que distraiga del objetivo.',
        'Agrega una casilla de verificación por paso para marcar lo terminado.',
        'Usa instrucciones directas que empiecen con un verbo (Mira, Cuenta, Escribe).',
    ],
    Condition.ASD: [
        'Usa lenguaje literal: sin metáforas, ironías, refranes ni dobles sentidos.',
        'Presenta una secuencia clara: Primero, Después, Al final.',
        'Explica de forma explícita qué se espera como respuesta y cuándo termina la actividad.',
        'Mantén una estructura predecible y repetida en todos los pasos.',
        'Evita cambios bruscos de tema dentro de la misma actividad.',
    ],
    Condition.ID_MILD: [
        'Aplica Lectura Fácil: frases muy cortas, una idea por frase y palabras de uso diario.',
        'Reduce la abstracción con ejemplos concretos de la vida cotidiana.',
        'Mantén el objetivo de aprendizaje, pero con menos ejercicios y apoyos en cada paso.',
        'Explica las palabras difíciles en un glosario sencillo.',
    ],
    Condition.ID_MODERATE: [
        'Aplica Lectura Fácil estricta: frases de máximo 10 palabras y vocabulario básico.',
        'Prioriza el objetivo esencial de la actividad; reduce de forma significativa la cantidad de ejercicios.',
        'Cada paso debe tener un apoyo visual descrito y un ejemplo resuelto.',
        'Usa respuestas cerradas o de selección en lugar de respuestas abiertas.',
    ],
}

ADJUSTMENT_RULES = {
    UdlAdjustment.ACCESSIBLE_FONT: 'Escribe pensando en una tipografía grande y espaciada: frases cortas y sin abreviaturas.',
    UdlAdjustment.SEGMENTATION: 'Divide los enunciados complejos en pasos guiados con metas claras.',
    UdlAdjustment.VISUAL_SUPPORT: 'Cuando ayude a comprender, describe un apoyo visual concreto en el campo "visual_support" (no inventes datos para hacerlo).',
    UdlAdjustment.STEP_SEQUENCE: 'Organiza la actividad como una secuencia numerada de principio a fin.',
    UdlAdjustment.EASY_READ: 'Aplica pautas de Lectura Fácil en todo el texto.',
    UdlAdjustment.FEWER_DISTRACTIONS: 'Elimina información, adornos o datos que no sean necesarios para la tarea.',
    UdlAdjustment.HIGH_CONTRAST: 'Indica las palabras clave que deben destacarse con alto contraste en el campo "keyword".',
}


# Always sent, whatever the condition: they protect the content of the original worksheet
GENERAL_RULES = [
    'Conserva todos los datos de la guía original: personajes, números, cantidades y preguntas. '
    'Nunca agregues ni cambies números, cantidades, nombres o hechos que no estén en el original.',
    'Aplica una regla solo si corresponde al contenido; si una regla no tiene sentido para esta guía, ignórala.',
    'Cada pregunta o ejercicio del original debe seguir presente en la adaptación (puede dividirse en pasos).',
]


# When a student has several conditions their rules are applied together; if two of them pull in
# opposite directions, the one that protects the student more wins
PROTECTIVE_ORDER = [Condition.ID_MODERATE, Condition.ID_MILD, Condition.ASD, Condition.ADHD,
                    Condition.DYSCALCULIA, Condition.DYSLEXIA]


def rules_for(conditions, adjustments, subject_code=None, grade=None):
    """
    Plain-text rules for all the student's conditions (one list, or a single one), the UDL adjustments
    the teacher chose, the subject and the grade.
    """
    conditions = [conditions] if isinstance(conditions, str) else list(conditions)
    conditions.sort(key=PROTECTIVE_ORDER.index)
    lines = ['Reglas generales (siempre):'] + [f'- {rule}' for rule in GENERAL_RULES]
    labels = ' + '.join(Condition(value).label for value in conditions)
    lines.append(f'Condiciones del estudiante: {labels}.')
    if len(conditions) > 1:
        lines.append('El estudiante tiene varias condiciones: una sola guía debe cumplir las reglas de TODAS a la '
                     'vez. Si dos reglas se contradicen, aplica la de la condición que aparece primero (la que '
                     'más protege al estudiante). En las justificaciones ("grade_reason", "udl_justification") '
                     f'explica en una frase cómo atiendes cada una: {labels}.')
    for condition in conditions:
        lines.append(f'Reglas obligatorias para {Condition(condition).label}:')
        lines += [f'- {rule}' for rule in CONDITION_RULES[condition]]
    if adjustments:
        lines.append('Ajustes DUA seleccionados por el docente:')
        lines += [f'- {UdlAdjustment(value).label}: {ADJUSTMENT_RULES[value]}' for value in adjustments]
    lines += subject_and_level_rules(subject_code, grade)
    return '\n'.join(lines)
