"""
Subject and grade of the Ecuadorian curriculum (EGB) applied to the adaptation.

- The grade is grouped into its subnivel (MINEDUC-ME-2016-00020-A): Preparatoria (1.º, integrated
  curriculum by learning areas), Elemental (2.º-4.º), Media (5.º-7.º) and Superior (8.º-10.º).
- Each subject adds its own UDL strategies to the prompt, and each subnivel the reading level expected.
- The analysis tells which subject and subnivel the worksheet really belongs to, so the teacher is warned
  when it does not match what was registered or the student's grade.
The rule texts are in Spanish because the model writes the worksheet in Spanish.
"""
from apps.core.choices import Grade

SUBNIVELS = {
    'PREPARATORIA': 'Preparatoria (1.º de EGB)',
    'ELEMENTAL': 'Elemental (2.º a 4.º de EGB)',
    'MEDIA': 'Media (5.º a 7.º de EGB)',
    'SUPERIOR': 'Superior (8.º a 10.º de EGB)',
    'BACHILLERATO': 'Bachillerato',
}
LEVEL_ORDER = list(SUBNIVELS)

GRADE_SUBNIVEL = {
    Grade.EGB_1: 'PREPARATORIA',
    Grade.EGB_2: 'ELEMENTAL', Grade.EGB_3: 'ELEMENTAL', Grade.EGB_4: 'ELEMENTAL',
    Grade.EGB_5: 'MEDIA', Grade.EGB_6: 'MEDIA', Grade.EGB_7: 'MEDIA',
    Grade.EGB_8: 'SUPERIOR', Grade.EGB_9: 'SUPERIOR', Grade.EGB_10: 'SUPERIOR',
}

# Subject codes of the catalog (cat_subject); a subject added later simply gets no extra rules
SUBJECT_RULES = {
    'MAT': [
        'Sigue la secuencia concreto → gráfico → abstracto: primero objetos o dibujos que se pueden contar, '
        'después su representación y al final el número o la operación.',
        'Un solo procedimiento por paso, con un ejemplo resuelto antes de que el estudiante lo intente.',
        'Conserva exactamente los números, unidades y operaciones de la guía.',
    ],
    'LEN': [
        'Antes de leer, presenta las palabras clave y de qué trata el texto; divide la lectura en partes cortas '
        'con una pregunta después de cada parte.',
        'Primero preguntas literales (qué, quién, dónde) y después las de inferencia.',
        'Para escribir, da un modelo o el inicio de la frase.',
    ],
    'CIE': [
        'Sigue el ciclo observar → preguntar → comprobar y relaciona cada concepto con algo que el estudiante '
        'puede ver o tocar en su entorno.',
        'Cada término científico va con una explicación sencilla y, si existe, con su figura.',
    ],
    'SOC': [
        'Ubica los hechos con apoyos espaciales o temporales (mapa sencillo, línea de tiempo) y relaciónalos '
        'con la familia, la escuela o la comunidad del estudiante.',
        'Usa solo las fechas y nombres indispensables.',
    ],
    'ING': [
        'Presenta el vocabulario nuevo con imagen y su significado en español; usa frases modelo cortas para '
        'repetir y completar.',
        'Las consignas van en español; lo que se practica va en inglés.',
    ],
    'ART': [
        'Instrucciones por pasos con apoyo visual y la lista de materiales al inicio.',
        'Valora el proceso y la expresión del estudiante, no un resultado perfecto.',
    ],
}

LEVEL_RULES = {
    'PREPARATORIA': 'Subnivel Preparatoria (currículo integrador por ámbitos): el estudiante todavía no lee de '
                    'forma autónoma; las consignas deben poder leerse en voz alta, ser muy cortas y pedir '
                    'señalar, unir, pintar o manipular.',
    'ELEMENTAL': 'Subnivel Elemental: la lectoescritura se está consolidando; frases de máximo 10 palabras, '
                 'preguntas con opciones y respuestas cortas.',
    'MEDIA': 'Subnivel Medio: puede leer consignas cortas solo; acepta respuestas de una o dos frases y tablas simples.',
    'SUPERIOR': 'Subnivel Superior: puede trabajar textos más largos divididos en partes y organizadores '
                'gráficos, con el vocabulario académico explicado.',
}


def subnivel_of(grade):
    return GRADE_SUBNIVEL.get(grade)


def subject_and_level_rules(subject_code, grade):
    """Extra prompt lines for the subject and the student's subnivel."""
    lines = []
    if subject_code in SUBJECT_RULES:
        lines.append('Estrategias para esta materia:')
        lines += [f'- {rule}' for rule in SUBJECT_RULES[subject_code]]
    level = subnivel_of(grade)
    if level in LEVEL_RULES:
        lines.append(f'Nivel del estudiante: {LEVEL_RULES[level]}')
    return lines


def level_gap(analysis, grade):
    """Subnivels between the worksheet and the student (+ the worksheet is harder), or None if unknown."""
    detected, student = analysis.get('detected_level'), subnivel_of(grade)
    if detected not in LEVEL_ORDER or student not in LEVEL_ORDER:
        return None
    return LEVEL_ORDER.index(detected) - LEVEL_ORDER.index(student)


def level_note(analysis, grade):
    """Instruction for the AI when the worksheet was written for another subnivel."""
    gap = level_gap(analysis, grade)
    if gap and gap > 0:
        return ('La guía está pensada para un subnivel superior al del estudiante: conserva el tema pero trabaja '
                'solo los aprendizajes básicos imprescindibles y baja la exigencia de lectura y de cálculo.')
    if gap and gap < 0:
        return ('La guía está pensada para un subnivel inferior al del estudiante: puedes mantener sus actividades '
                'y sumar un reto acorde a su grado, sin quitar los apoyos de su condición.')
    return ''


def curriculum_warnings(material, students):
    """Notices for the teacher when the worksheet does not match its subject or the students' grade."""
    analysis = material.ai_analysis or {}
    warnings = []
    detected = analysis.get('detected_subject')
    if detected and detected not in ('OTRA', material.subject.code):
        warnings.append(
            f'La guía parece de {SUBJECT_NAMES.get(detected, detected)}, pero la registraste en '
            f'{material.subject.name}. Revisa la materia: la IA adapta con las estrategias de la materia registrada.'
        )
    level = analysis.get('detected_level')
    for student in students:
        gap = level_gap(analysis, student.grade)
        if gap:
            relation = 'más avanzada' if gap > 0 else 'más básica'
            warnings.append(
                f'La guía parece del subnivel {SUBNIVELS[level]}, {relation} que el grado de {student.name} '
                f'({student.get_grade_display()}). La adaptación lo tuvo en cuenta; revisa que el contenido sea pertinente.'
            )
    return warnings


SUBJECT_NAMES = {
    'MAT': 'Matemática', 'LEN': 'Lengua y Literatura', 'CIE': 'Ciencias Naturales',
    'SOC': 'Estudios Sociales', 'ING': 'Inglés', 'ART': 'Educación Cultural y Artística',
}
