"""
One module per SEN condition (NEE) for the adapted worksheet.

The rules of udl_rules.py tell the AI what to do; a module makes them verifiable:

- fields: what the condition adds to the worksheet's JSON, required, so the AI cannot leave it out
  (a routine for ASD, minutes per block for ADHD, a worked example for intellectual disability...);
- instructions: how to fill those fields, in the prompt;
- checks: done in code after the answer (sentence length, a keyword in every step, no figurative
  language...). If a check fails the AI is asked once to correct exactly that.

A student with several conditions gets the fields and checks of all of them; where two limits differ, the
stricter one applies (the same "most protective wins" order as udl_rules.PROTECTIVE_ORDER).
"""
import copy
import re
import unicodedata

from apps.core.choices import Condition

STRING = {'type': 'STRING'}
INTEGER = {'type': 'INTEGER'}

# ---------- What each condition adds to the JSON ----------

MODULES = {
    Condition.DYSLEXIA: {
        'step_fields': {},
        'step_required': ['keyword'],
        'fields': {'reading_words': {'type': 'ARRAY', 'items': {
            'type': 'OBJECT', 'properties': {'word': STRING, 'syllables': STRING}, 'required': ['word', 'syllables']}}},
        'required': ['reading_words'],
        'instructions': (
            'Dislexia: cada paso lleva su "keyword" (la palabra clave de la consigna). En "reading_words" pon de 3 a '
            '6 palabras largas o difíciles de la guía con sus sílabas separadas por guiones (por ejemplo '
            '{"word": "membrana", "syllables": "mem-bra-na"}).'
        ),
        'sentence_limit': 15,
    },
    Condition.DYSCALCULIA: {
        'step_fields': {'concrete_support': STRING},
        'step_required': ['concrete_support'],
        'fields': {},
        'required': [],
        'instructions': (
            'Discalculia: en cada paso que tiene números o cantidades, "concrete_support" dice con qué material '
            'concreto o dibujo se representa esa cantidad (por ejemplo "12 tapas repartidas en 3 vasos"); en los '
            'pasos sin números déjalo vacío. Un solo cálculo por paso.'
        ),
    },
    Condition.ADHD: {
        'step_fields': {'minutes': INTEGER},
        'step_required': ['minutes'],
        'fields': {'active_break': STRING, 'break_after_step': INTEGER},
        'required': ['active_break', 'break_after_step'],
        'instructions': (
            'TDAH: cada paso es un bloque corto; en "minutes" indica cuántos minutos toma (de 1 a 8). En '
            '"active_break" describe una pausa activa de 1 minuto (por ejemplo "Ponte de pie, estira los brazos y '
            'respira 3 veces") y en "break_after_step" el número del paso después del cual se hace, a la mitad de '
            'la ficha. En "intro" di cuántos pasos tiene la ficha.'
        ),
        'max_minutes': 8,
    },
    Condition.ASD: {
        'step_fields': {},
        'step_required': [],
        'fields': {'routine': {'type': 'OBJECT', 'properties': {'first': STRING, 'then': STRING, 'last': STRING},
                               'required': ['first', 'then', 'last']},
                   'finish_signal': STRING},
        'required': ['routine', 'finish_signal'],
        'instructions': (
            'TEA: en "routine" escribe la rutina de la ficha en tres frases literales: "first" (Primero…), "then" '
            '(Después…) y "last" (Al final…). En "finish_signal" di con exactitud cuándo termina la actividad (por '
            'ejemplo "Terminaste cuando marcaste los 4 pasos y respondiste las 2 preguntas"). Todos los pasos con '
            'la misma estructura y lenguaje literal: sin metáforas, refranes ni ironías.'
        ),
    },
    Condition.ID_MILD: {
        'step_fields': {'worked_example': STRING},
        'step_required': ['worked_example'],
        'fields': {},
        'required': ['glossary'],
        'instructions': (
            'Discapacidad intelectual leve: cada paso lleva en "worked_example" un ejemplo ya resuelto del mismo '
            'tipo de actividad, que el estudiante ve antes de intentarlo, con OTROS datos: nunca la respuesta ni las '
            'opciones del paso (si el paso pide las regiones del Ecuador, el ejemplo usa otra cosa, como los días '
            'de la semana). El "glossary" explica con palabras de uso diario de 2 a 5 palabras difíciles.'
        ),
        'sentence_limit': 15,
    },
    Condition.ID_MODERATE: {
        'step_fields': {'worked_example': STRING},
        'step_required': ['worked_example'],
        'fields': {},
        'required': ['glossary'],
        'instructions': (
            'Discapacidad intelectual moderada: como máximo 4 pasos, solo los del objetivo esencial. Cada paso '
            'lleva en "worked_example" un ejemplo ya resuelto del mismo tipo de actividad con OTROS datos (nunca la '
            'respuesta ni las opciones del paso) y se responde eligiendo o marcando ("answer_type" SELECCION o MARCAR), nunca escribiendo. '
            'Frases de máximo 10 palabras. El "glossary" explica de 2 a 5 palabras difíciles.'
        ),
        'sentence_limit': 10,
        'max_steps': 4,
        'closed_answers': True,
    },
}


def total_minutes(content):
    """Minutes of the whole worksheet when its steps carry them (ADHD), else None."""
    minutes = [s.get('minutes') for s in content.get('steps', []) if isinstance(s.get('minutes'), int)]
    return sum(minutes) or None


def modules_for(conditions):
    return [MODULES[Condition(value)] for value in conditions]


def schema_for(base_schema, conditions):
    """The worksheet JSON schema with the fields of every condition of the student, required."""
    schema = copy.deepcopy(base_schema)
    step = schema['properties']['steps']['items']
    for module in modules_for(conditions):
        step['properties'].update(copy.deepcopy(module['step_fields']))
        step['required'] = list(dict.fromkeys(step['required'] + module['step_required']))
        schema['properties'].update(copy.deepcopy(module['fields']))
        schema['required'] = list(dict.fromkeys(schema['required'] + module['required']))
        if module.get('closed_answers'):
            step['properties']['answer_type'] = {'type': 'STRING', 'enum': ['SELECCION', 'MARCAR', 'NINGUNA']}
    return schema


def sentence_limit(conditions):
    """The strictest words-per-sentence limit of the student's conditions, or None."""
    limits = [m['sentence_limit'] for m in modules_for(conditions) if m.get('sentence_limit')]
    return min(limits) if limits else None


def instructions_for(conditions):
    """How to fill the condition fields, for the prompt."""
    lines = [module['instructions'] for module in modules_for(conditions)]
    limit = sentence_limit(conditions)
    if limit:
        lines.append(f'Cada frase que lee el estudiante tiene como máximo {limit} palabras, también las de '
                     '"explanation", "example", "intro", "hint" y "worked_example": si una idea es larga, divídela en '
                     'dos frases (por ejemplo "Ana y Quito son sustantivos propios. Nombran a una persona y a una '
                     'ciudad.").')
    return 'Campos propios de las condiciones del estudiante (obligatorios):\n' + '\n'.join(f'- {line}' for line in lines)


# ---------- Checks after the answer ----------

SENTENCE_END = re.compile(r'[.!?;:\n]+')
WORD = re.compile(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+')
DIGIT = re.compile(r'\d')
# Common Spanish figurative expressions a literal reader (ASD) would misread
FIGURATIVE = [
    'pan comido', 'manos a la obra', 'ponte las pilas', 'echar una mano', 'echa una mano', 'en un abrir y cerrar de ojos',
    'como pez en el agua', 'en las nubes', 'cuesta arriba', 'dar en el clavo', 'da en el clavo', 'con las manos en la masa',
    'a la primera de cambio', 'romper el hielo', 'rompe el hielo', 'meter la pata', 'pan de cada dia', 'vuela tu imaginacion',
    'a toda maquina', 'pilas puestas', 'mas facil que', 'pedazo de pastel', 'como anillo al dedo', 'estar en la luna',
]


def _plain(text):
    text = unicodedata.normalize('NFKD', str(text or ''))
    return ''.join(c for c in text if not unicodedata.combining(c)).lower()


def _student_texts(content):
    """The texts the student reads (not the teacher's justification)."""
    texts = [content.get('intro', ''), content.get('learning_goal', ''), content.get('closing_message', '')]
    for concept in content.get('concepts', []):
        texts += [concept.get('explanation', ''), concept.get('example', '')]
    for step in content.get('steps', []):
        texts += [step.get('instruction', ''), step.get('hint', ''), step.get('worked_example', '')]
    return [t for t in texts if t]


def long_sentences(content, limit):
    found = []
    for text in _student_texts(content):
        for sentence in SENTENCE_END.split(text):
            if len(WORD.findall(sentence)) > limit:
                found.append(sentence.strip())
    return found


def reveals_answer(step):
    """A worked example that names the answer, or two or more of the step's own options, gives it away."""
    example = _plain(step.get('worked_example', ''))
    if not example:
        return False
    if re.search(r'\b(respuesta|opcion correcta|la correcta es)\b', example):
        return True
    options = [_plain(option).strip() for option in step.get('options', []) if len(str(option).strip()) > 2]
    return sum(1 for option in options if option in example) >= 2


# Words that always carry an accent in Spanish, so a version without it is a spelling mistake: every "-ción"
# noun, and common words with no unaccented twin (not "esta", "como", "que"...). Not "-sión": English words
# such as "television" would be flagged in an English worksheet.
ALWAYS_ACCENTED = re.compile(
    r'\b(?:\w+cion|despues|tambien|ademas|aqui|alli|ultimo|ultima|numero|numeros|linea|lineas|'
    r'pais|paises|arbol|arboles|facil|dificil|rapido|rapida|musica|matematica|matematicas|geografia|energia|dia|'
    r'dias|raiz|maiz|ingles|pagina|paginas|caracteristica|caracteristicas|sabado|miercoles|telefono|simbolo|'
    r'publico|economia|biologia|tecnologia|fisica|quimica|organo|organos|celula|celulas|oceano|oceanos|'
    r'region)\b')


def missing_accents(content):
    """Words written without the accent they always need (the student learns to read with this text)."""
    found = []
    for text in _student_texts(content) + [q.get('question', '') for q in content.get('check_questions', [])]:
        for word in re.findall(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+', str(text)):
            if word.isascii() and ALWAYS_ACCENTED.fullmatch(word.lower()) and word.lower() not in found:
                found.append(word.lower())
    return found


def problems(content, conditions):
    """What the answer misses of the student's condition modules ([] when it complies)."""
    found = []
    steps = content.get('steps', [])
    present = set(conditions)
    # For every student: the worksheet is a reading model
    if unaccented := missing_accents(content):
        found.append(f'faltan tildes en: {", ".join(unaccented[:8])}; escribe todas las palabras con sus tildes')
    limit = sentence_limit(conditions)
    if limit:
        long = long_sentences(content, limit)
        if long:
            examples = '; '.join(f'«{s}»' for s in long[:4])
            found.append(f'{len(long)} frase(s) pasan de {limit} palabras; divídelas en frases más cortas: {examples}')
    if Condition.DYSLEXIA in present:
        missing = [n for n, s in enumerate(steps, 1) if not str(s.get('keyword', '')).strip()]
        if missing:
            found.append(f'los pasos {", ".join(map(str, missing))} no tienen "keyword"')
        words = content.get('reading_words') or []
        if not (2 <= len(words) <= 8) or any('-' not in str(w.get('syllables', '')) for w in words):
            found.append('"reading_words" debe tener de 3 a 6 palabras con sus sílabas separadas por guiones')
    if Condition.DYSCALCULIA in present:
        missing = [n for n, s in enumerate(steps, 1)
                   if DIGIT.search(str(s.get('instruction', ''))) and not str(s.get('concrete_support', '')).strip()]
        if missing:
            found.append(f'los pasos {", ".join(map(str, missing))} tienen números pero no "concrete_support"')
    if Condition.ADHD in present:
        limit = MODULES[Condition.ADHD]['max_minutes']
        wrong = [n for n, s in enumerate(steps, 1) if not isinstance(s.get('minutes'), int) or not 1 <= s['minutes'] <= limit]
        if wrong:
            found.append(f'los pasos {", ".join(map(str, wrong))} deben durar de 1 a {limit} minutos ("minutes")')
        if not str(content.get('active_break', '')).strip():
            found.append('falta la pausa activa ("active_break")')
        after = content.get('break_after_step')
        if steps and not (isinstance(after, int) and 1 <= after <= len(steps)):
            found.append('"break_after_step" debe ser el número de uno de los pasos')
    if Condition.ASD in present:
        routine = content.get('routine') or {}
        if not all(str(routine.get(key, '')).strip() for key in ('first', 'then', 'last')):
            found.append('la rutina ("routine") debe tener "first", "then" y "last"')
        if not str(content.get('finish_signal', '')).strip():
            found.append('falta decir cuándo termina la actividad ("finish_signal")')
        text = _plain(' '.join(_student_texts(content)))
        figurative = [f for f in FIGURATIVE if f in text]
        if figurative:
            found.append(f'usa lenguaje figurado, reescríbelo de forma literal: {", ".join(figurative)}')
    if present & {Condition.ID_MILD, Condition.ID_MODERATE}:
        missing = [n for n, s in enumerate(steps, 1) if not str(s.get('worked_example', '')).strip()]
        if missing:
            found.append(f'los pasos {", ".join(map(str, missing))} no tienen ejemplo resuelto ("worked_example")')
        revealing = [n for n, s in enumerate(steps, 1) if reveals_answer(s)]
        if revealing:
            found.append(f'el ejemplo resuelto de los pasos {", ".join(map(str, revealing))} repite la respuesta o las '
                         'opciones del paso; usa un ejemplo con otros datos')
        if not content.get('glossary'):
            found.append('falta el glosario ("glossary") con las palabras difíciles')
    if Condition.ID_MODERATE in present:
        limit = MODULES[Condition.ID_MODERATE]['max_steps']
        if len(steps) > limit:
            found.append(f'tiene {len(steps)} pasos; deja como máximo {limit}, solo los del objetivo esencial')
        open_steps = [n for n, s in enumerate(steps, 1) if s.get('answer_type') == 'ESPACIO']
        if open_steps:
            found.append(f'los pasos {", ".join(map(str, open_steps))} piden escribir; usa SELECCION o MARCAR')
    return found
