"""
Google Gemini client (REST API) and the three prompts of the system:

1. analyze_material: reads the worksheet ONCE (page images and/or text) with the multimodal
   model and returns its text, a description and location of every figure and the barriers detected.
2. adapt_worksheet: restructures that content for one SEN condition following the
   UDL rules of udl_rules.py. It only receives text, never the original file again.
3. recommend_next_steps: reads the teacher's classroom evaluation and advises how to adjust
   the next adaptation.
4. analyze_lesson / adapt_lesson ("Adaptar clase"): read one session of the teacher's class plan
   (plus the teacher guide pages, if any) once, then adapt the whole class for each student.

Privacy (LOPDP): the model never receives the student's name, notes or birth date;
only the grade, the condition and the UDL adjustments.
"""
import json
import logging
import re
import time
import urllib.error
import urllib.request

from django.conf import settings

from apps.core.choices import AdaptationGrade, Condition, Grade

from . import visuals
from .curriculum import level_note
from .udl_rules import rules_for

logger = logging.getLogger('maestro_sombra')

API_URL = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
RETRYABLE_STATUS = {429, 500, 503}


class AIError(Exception):
    """Any problem talking to Gemini or reading its answer. The message is shown to the teacher."""


# ---------- JSON schemas the model must follow ----------

ANALYSIS_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'readable': {'type': 'BOOLEAN'},
        'problem': {'type': 'STRING'},
        'extracted_text': {'type': 'STRING'},
        'figures': {'type': 'ARRAY', 'items': {
            'type': 'OBJECT',
            'properties': {
                'description': {'type': 'STRING'},
                'source': {'type': 'INTEGER'},
                'box_2d': {'type': 'ARRAY', 'items': {'type': 'INTEGER'}},
            },
            'required': ['description'],
        }},
        'exercise_count': {'type': 'INTEGER'},
        'detected_barriers': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
        'cognitive_load': {'type': 'STRING', 'enum': ['BAJA', 'MEDIA', 'ALTA']},
        'summary': {'type': 'STRING'},
        # What the worksheet really is, to warn the teacher if it does not match the registered subject or grade
        'detected_subject': {'type': 'STRING', 'enum': ['MAT', 'LEN', 'CIE', 'SOC', 'ING', 'ART', 'OTRA']},
        'detected_level': {'type': 'STRING', 'enum': ['PREPARATORIA', 'ELEMENTAL', 'MEDIA', 'SUPERIOR', 'BACHILLERATO']},
    },
    'required': ['readable', 'extracted_text', 'figures', 'detected_barriers', 'cognitive_load', 'summary',
                 'detected_subject', 'detected_level'],
}

VISUAL_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'type': {'type': 'STRING', 'enum': visuals.TYPES},
        'parts': {'type': 'INTEGER'},
        'shaded': {'type': 'INTEGER'},
        'crossed': {'type': 'INTEGER'},
        'start': {'type': 'INTEGER'},
        'end': {'type': 'INTEGER'},
        'marks': {'type': 'ARRAY', 'items': {'type': 'INTEGER'}},
        'count': {'type': 'INTEGER'},
        'items': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
    },
    'required': ['type'],
}

ADAPTATION_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'title': {'type': 'STRING'},
        'subtitle': {'type': 'STRING'},
        'summary': {'type': 'STRING'},
        'intro': {'type': 'STRING'},
        'learning_goal': {'type': 'STRING'},
        'concepts': {'type': 'ARRAY', 'items': {
            'type': 'OBJECT',
            'properties': {
                'title': {'type': 'STRING'},
                'explanation': {'type': 'STRING'},
                'example': {'type': 'STRING'},
                'figure_number': {'type': 'INTEGER'},
                'visual': VISUAL_SCHEMA,
            },
            'required': ['title', 'explanation', 'example'],
        }},
        'steps': {'type': 'ARRAY', 'items': {
            'type': 'OBJECT',
            'properties': {
                'title': {'type': 'STRING'},
                'instruction': {'type': 'STRING'},
                'hint': {'type': 'STRING'},
                'keyword': {'type': 'STRING'},
                'visual_support': {'type': 'STRING'},
                'figure_description': {'type': 'STRING'},
                'answer_type': {'type': 'STRING', 'enum': ['ESPACIO', 'SELECCION', 'MARCAR', 'NINGUNA']},
                'options': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                'figure_number': {'type': 'INTEGER'},
                'visual': VISUAL_SCHEMA,
            },
            'required': ['title', 'instruction', 'hint', 'answer_type'],
        }},
        'check_questions': {'type': 'ARRAY', 'items': {
            'type': 'OBJECT',
            'properties': {
                'question': {'type': 'STRING'},
                'options': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                'answer': {'type': 'STRING'},
            },
            'required': ['question', 'options', 'answer'],
        }},
        'glossary': {'type': 'ARRAY', 'items': {
            'type': 'OBJECT',
            'properties': {'term': {'type': 'STRING'}, 'meaning': {'type': 'STRING'}},
            'required': ['term', 'meaning'],
        }},
        'closing_message': {'type': 'STRING'},
        'udl_principle': {'type': 'STRING'},
        'udl_justification': {'type': 'STRING'},
    },
    'required': ['title', 'summary', 'intro', 'learning_goal', 'concepts', 'steps', 'check_questions',
                 'udl_principle', 'udl_justification'],
}

ANALYSIS_SYSTEM = (
    'Eres un asistente pedagógico experto en educación inclusiva del Ecuador. Analizas guías y fichas '
    'escolares de Educación General Básica. En "extracted_text" recoge con tus propias palabras el contenido '
    'necesario para adaptar la guía: las consignas, las preguntas y los ejercicios completos, los datos y '
    'números exactos, y los conceptos clave. No copies párrafos largos literalmente (muchas guías vienen de '
    'libros con derechos de autor); las consignas y preguntas cortas sí pueden ir tal cual. Describe con palabras '
    'cada dibujo, diagrama o figura (qué muestra, cantidades, colores y su relación con el ejercicio); '
    'indica en "source" el número de la imagen donde está y en "box_2d" su recuadro [ymin, xmin, ymax, xmax] '
    'en escala de 0 a 1000, que encierre solo la figura y no el texto que la rodea. Cuenta también como '
    'figura todo material de actividad que el estudiante necesita ver o completar: tablas para llenar, sopas '
    'de letras, crucigramas, plantillas para recortar o armar, esquemas para completar y líneas de tiempo. '
    'Señala además las barreras '
    'de aprendizaje que el material presenta para estudiantes con necesidades educativas especiales. '
    'Si la imagen está borrosa, cortada o no contiene una guía escolar, responde readable=false y explica '
    'el problema en "problem". Escribe siempre en español.'
)

ADAPTATION_SYSTEM = (
    'Eres un docente experto en Diseño Universal para el Aprendizaje (DUA) y educación inclusiva en Ecuador. '
    'Tu objetivo NO es reescribir ni describir la guía: es que el estudiante con necesidades educativas '
    'especiales APRENDA el tema y pueda HACER la tarea de la guía. Para eso: (1) le explicas los conceptos '
    'que necesita con palabras sencillas y ejemplos de su vida diaria; (2) lo guías paso a paso en las '
    'actividades de la guía, con pistas que lo ayuden a pensar sin darle la respuesta; y (3) compruebas con '
    'preguntas cortas que entendió. Mantienes el mismo objetivo de aprendizaje de la guía original. No '
    'inventes datos que no estén en la guía. Las figuras sirven para explicar: úsalas como apoyo, nunca como '
    'la actividad en sí. No des formato visual (tipografía, colores, tamaños): el sistema lo aplica. Escribe '
    'en español del Ecuador con ortografía correcta, incluidas todas las tildes y los signos ¿ ¡, dirigiéndote '
    'al estudiante de "tú". La adaptación será revisada por el docente.'
)

PEDAGOGY_RULES = (
    'Estructura obligatoria de la guía adaptada:\n'
    '- "learning_goal": una frase corta que empiece con "Vas a aprender…".\n'
    '- "concepts" (APRENDE): de 2 a 5 conceptos esenciales que el estudiante necesita para hacer la tarea, '
    'elegidos según los objetivos de la guía. Para cada uno: "explanation" en 2 o 3 frases cortas y sencillas '
    'que expliquen qué es y para qué sirve (no qué muestra una imagen), y "example" con una comparación de la '
    'vida diaria (por ejemplo, la membrana es como la piel o la puerta de la casa). Si una figura de la guía '
    'ayuda a entender ese concepto, pon su número en "figure_number".\n'
    '- "steps" (AHORA PRACTICA): las actividades y preguntas de la guía original, adaptadas. Cada paso dice '
    'qué hacer y cómo hacerlo, con una "hint" (pista) que remite a lo aprendido en "concepts". Si la guía '
    'no trae actividades, crea de 2 a 4 actividades cortas para practicar el contenido de la guía (completar, '
    'unir, elegir o clasificar), sin datos nuevos.\n'
    '- "check_questions" (COMPRUEBA LO QUE APRENDISTE): de 2 a 3 preguntas de opción múltiple con 3 opciones '
    'y su "answer" correcta, sobre los conceptos explicados.\n'
    '- Nunca escribas una instrucción que solo diga "Observa la figura" o que describa la imagen: la '
    'descripción para accesibilidad va únicamente en "figure_description".'
)

IMAGE_RULES = (
    'Imágenes: si un concepto o un paso usa una figura de la guía original, escribe su número en "figure_number" '
    '(0 si ninguna); usa cada figura una sola vez y solo si ayuda a entender. Si una actividad necesita un '
    'material de la guía para hacerse (una tabla para completar, una sopa de letras, una plantilla, un dibujo '
    'para colorear o señalar), ese paso DEBE llevar el "figure_number" de esa figura. Si un concepto o paso necesita '
    'una ilustración que la guía no tiene, elige en "visual" uno de estos '
    'tipos usando SOLO datos de la guía: FRACTION_CIRCLE o FRACTION_BAR (parts, shaded y crossed para lo que '
    'se quita o se comparte), NUMBER_LINE (start, end y marks), COUNT (count y crossed) o SEQUENCE (items: de '
    '2 a 5 frases muy cortas con el orden de la actividad). Si ningún tipo sirve, usa NONE. Máximo 3 pasos '
    'con "visual", y nunca uno que contradiga los datos de la guía. Si el texto menciona un dibujo, imagen o '
    'figura que NO está en la lista de figuras, no le pidas al estudiante que lo mire: reescribe la consigna '
    'para que se entienda sin ese dibujo o usa un "visual". Cada paso debe tener un título corto que diga qué '
    'se hace (por ejemplo "Observa la célula animal"), nunca solo "Paso 1".'
)


def _call(model, system, parts, schema, timeout):
    body = {
        'systemInstruction': {'parts': [{'text': system}]},
        'contents': [{'role': 'user', 'parts': parts}],
        'generationConfig': {
            'responseMimeType': 'application/json',
            'responseSchema': schema,
            'temperature': 0.3,
        },
    }
    request = urllib.request.Request(
        API_URL.format(model=model),
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'x-goog-api-key': settings.GEMINI_API_KEY},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def generate_json(system, parts, schema):
    """Calls Gemini and returns (data, model_version). Falls back to the lite model if overloaded."""
    if not settings.GEMINI_API_KEY:
        raise AIError('Falta configurar la clave de la API de Gemini (GEMINI_API_KEY en el archivo .env).')
    last_error = None
    # One quick try with the main model; if it is overloaded go straight to the fallback,
    # which gets a second chance. This keeps the wait for the teacher short.
    plan = ((settings.GEMINI_MODEL, 1, settings.GEMINI_PRIMARY_TIMEOUT_SECONDS),
            (settings.GEMINI_FALLBACK_MODEL, 2, settings.GEMINI_TIMEOUT_SECONDS))
    for model, attempts, timeout in plan:
        for attempt in range(attempts):
            try:
                answer = _call(model, system, parts, schema, timeout)
                candidate = answer['candidates'][0]
                # The answer may come split in several parts (and "thought" parts carry no answer)
                text = ''.join(p.get('text', '') for p in candidate.get('content', {}).get('parts', [])
                               if not p.get('thought'))
                if not text:
                    logger.warning('Gemini empty answer with model %s (finishReason=%s)',
                                   model, candidate.get('finishReason'))
                    continue
                return json.loads(text), answer.get('modelVersion', model)
            except urllib.error.HTTPError as error:
                last_error = error
                logger.warning('Gemini HTTP %s with model %s (attempt %s)', error.code, model, attempt + 1)
                if error.code not in RETRYABLE_STATUS:
                    raise AIError('La IA rechazó la solicitud. Revisa la clave de Gemini o el archivo enviado.') from error
                if attempts > 1:
                    time.sleep(2)
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
                logger.warning('Gemini connection error with model %s: %s', model, error)
            except (KeyError, IndexError, json.JSONDecodeError) as error:
                last_error = error
                logger.warning('Gemini returned an unexpected answer with model %s: %s', model, repr(error)[:200])
    raise AIError('La IA no está disponible en este momento. Inténtalo de nuevo en unos minutos.') from last_error


def analyze_material(*, text='', image_parts=(), subject, topic):
    """Reads the worksheet once: its page images (photo, PDF pages, Word pictures) and/or its text."""
    prompt = (
        f'Materia: {subject}. Tema: {topic}.\n'
        'Analiza esta guía escolar: extrae su contenido, describe y ubica cada figura, y señala las barreras '
        'de aprendizaje, el número de ejercicios y la carga cognitiva (BAJA, MEDIA o ALTA). En "summary" '
        'resume en una frase qué trabaja la guía. Según su contenido (no según lo que dice el docente), '
        'indica en "detected_subject" la materia que realmente trabaja (MAT Matemática, LEN Lengua y Literatura, '
        'CIE Ciencias Naturales, SOC Estudios Sociales, ING Inglés, ART Educación Cultural y Artística u OTRA) y '
        'en "detected_level" el subnivel del currículo ecuatoriano para el que está pensada: PREPARATORIA (1.º), '
        'ELEMENTAL (2.º a 4.º), MEDIA (5.º a 7.º), SUPERIOR (8.º a 10.º) o BACHILLERATO.'
    )
    parts = [{'text': prompt}]
    if text:
        parts.append({'text': f'Texto de la guía:\n{text}'})
    parts += list(image_parts)
    data, model = generate_json(ANALYSIS_SYSTEM, parts, ANALYSIS_SCHEMA)
    if not data.get('readable', False):
        raise AIError(data.get('problem') or 'No se pudo leer el material. Toma una nueva foto o pega el texto.')
    return data, model


def adapt_worksheet(*, analysis, subject, topic, grade, condition, adjustments, simplify=False, feedback='',
                    subject_code=None):
    """Restructures the analyzed worksheet for ONE condition. Receives no personal data."""
    figures = analysis.get('figures', [])
    figure_lines = '\n'.join(f'Figura {n}: {f["description"]}' for n, f in enumerate(figures, start=1))
    prompt = [
        f'Materia: {subject}. Tema: {topic}. Grado: {Grade(grade).label}.',
        rules_for(condition, adjustments, subject_code, grade),
        level_note(analysis, grade),
        f'Contenido de la guía original:\n{analysis["extracted_text"]}',
        f'Figuras de la guía original:\n{figure_lines or "(la guía no tiene figuras)"}',
        'Barreras detectadas: ' + '; '.join(analysis.get('detected_barriers', [])),
        PEDAGOGY_RULES,
        IMAGE_RULES,
        'Devuelve la guía adaptada. En "summary" escribe una frase para el banco de adaptaciones. '
        'En "udl_principle" indica el principio DUA principal aplicado y en "udl_justification" explica '
        'en una o dos frases por qué la adaptación reduce las barreras.',
    ]
    if simplify:
        prompt.append('Esta es una REGENERACIÓN: la versión anterior resultó difícil. Simplifica más: menos '
                      'ejercicios, frases más cortas y más apoyos visuales, manteniendo el objetivo esencial.')
    if feedback:
        prompt.append(f'Observación del docente sobre la versión anterior: {feedback}')
    # Last on purpose: the model follows the final instruction most reliably
    prompt.append(SPELLING_RULE)
    content, model = generate_json(ADAPTATION_SYSTEM, [{'text': '\n\n'.join(part for part in prompt if part)}],
                                   ADAPTATION_SCHEMA)
    return normalize_adaptation(content, figure_count=len(figures)), model


RECOMMENDATION_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'recommendation': {'type': 'STRING'},
        'simplify': {'type': 'BOOLEAN'},
    },
    'required': ['recommendation', 'simplify'],
}

RECOMMENDATION_SYSTEM = (
    'Eres un asesor pedagógico experto en Diseño Universal para el Aprendizaje (DUA) y educación inclusiva en '
    'Ecuador. Lees cómo le fue a un estudiante con necesidades educativas especiales con una guía adaptada y '
    'recomiendas al docente sombra, en 2 a 4 frases concretas, cómo ajustar la próxima adaptación (cantidad de '
    'ejercicios, apoyos visuales, vocabulario, segmentación). Te diriges al docente de "usted", en español del '
    'Ecuador con ortografía correcta. En "simplify" indicas si conviene regenerar una versión más simple.'
)


def recommend_next_steps(*, subject, topic, grade, condition, adjustments, summary, evaluation):
    """Pedagogical advice from the teacher's classroom evaluation. Receives no personal data."""
    prompt = '\n'.join([
        f'Materia: {subject}. Tema: {topic}. Grado: {Grade(grade).label}. Condición: {condition_label(condition)}.',
        'Ajustes DUA aplicados: ' + (', '.join(adjustments) or 'ninguno'),
        f'Resumen de la guía adaptada: {summary or "(sin resumen)"}',
        'Resultado en el aula:',
        *[f'- {question}: {answer}' for question, answer in evaluation],
    ])
    data, model = generate_json(RECOMMENDATION_SYSTEM, [{'text': prompt}], RECOMMENDATION_SCHEMA)
    text = (data.get('recommendation') or '').strip()
    if not text:
        raise AIError('La IA no devolvió una recomendación. Inténtalo de nuevo.')
    return text, bool(data.get('simplify')), model


MOMENT_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'name': {'type': 'STRING'},
        'duration': {'type': 'STRING'},
        'activities': {'type': 'STRING'},
    },
    'required': ['name', 'activities'],
}

LESSON_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'title': {'type': 'STRING'},
        'skill': {'type': 'STRING'},
        'indicator': {'type': 'STRING'},
        'objective': {'type': 'STRING'},
        'moments': {'type': 'ARRAY', 'items': MOMENT_SCHEMA},
        'materials': {'type': 'STRING'},
        'metacognition': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
        'guide_support': {'type': 'STRING'},
        'summary': {'type': 'STRING'},
    },
    'required': ['title', 'objective', 'moments', 'summary'],
}

ADAPTED_MOMENT_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'name': {'type': 'STRING'},
        'duration': {'type': 'STRING'},
        'teacher_actions': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
        'student_task': {'type': 'STRING'},
        'supports': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
        'watch_for': {'type': 'STRING'},
    },
    'required': ['name', 'teacher_actions', 'student_task', 'supports'],
}

LESSON_ADAPTATION_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'suggested_grade': {'type': 'STRING', 'enum': ['ACCESS', 'NON_SIGNIFICANT', 'SIGNIFICANT']},
        'grade_reason': {'type': 'STRING'},
        'adapted_objective': {'type': 'STRING'},
        'plan_row': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
        'moments': {'type': 'ARRAY', 'items': ADAPTED_MOMENT_SCHEMA},
        'evaluation': {'type': 'STRING'},
        'metacognition': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
    },
    'required': ['suggested_grade', 'grade_reason', 'adapted_objective', 'plan_row', 'moments', 'evaluation'],
}

LESSON_SYSTEM = (
    'Eres un asesor pedagógico del Ecuador. Lees la planificación de UNA clase (sesión) escrita por un docente, '
    'en cualquier formato, y, si las hay, las páginas de la guía del docente de esa clase. Identifica la destreza '
    'con criterio de desempeño, el indicador de evaluación, el objetivo, los momentos de la clase en orden (por '
    'ejemplo Calentemos, Conversemos, Registremos, Transferencia, o Inicio, Desarrollo, Cierre) con su duración '
    'y actividades, los materiales y las preguntas de metacognición. Si la guía trae una sección de "Apoyo" '
    'para alumnos con dificultades, resúmela en "guide_support". No incluyas nombres de personas ni de '
    'instituciones. Escribe en español.'
)

LESSON_ADAPTATION_SYSTEM = (
    'Eres un docente experto en Diseño Universal para el Aprendizaje (DUA) y educación inclusiva en Ecuador que '
    'acompaña como docente sombra a un estudiante con necesidades educativas especiales. Adaptas una clase '
    'completa para ese estudiante, momento por momento, para que participe en la misma clase que sus compañeros. '
    'Grados de adaptación curricular: Grado 1 (de acceso): solo cambian los recursos, el espacio, el tiempo o la '
    'comunicación; el contenido y la evaluación son los mismos. Grado 2 (no significativa): cambian la metodología, '
    'las actividades, los recursos y la forma de evaluar, pero se mantienen la destreza y el objetivo. Grado 3 '
    '(significativa): se ajustan la destreza y el objetivo a un nivel más básico del mismo tema. Para cada momento '
    'indicas qué hace el docente sombra, qué hace el estudiante, los apoyos y qué señales de dificultad vigilar. '
    'En "plan_row" escribe de 3 a 6 acciones breves, en el estilo de la fila "Adaptación curricular" de una '
    'planificación (por ejemplo "Reducir la cantidad de ejercicios, priorizando…"), sin nombre ni iniciales del '
    'estudiante. Usa las sugerencias de "Apoyo" de la guía y deja de lado las de "Ampliación". No inventes datos '
    'que no estén en la clase. Escribe en español del Ecuador, dirigiéndote al docente.'
)


def analyze_lesson(*, session_text, guide_parts=(), book_summary='', subject, grade):
    """Reads one session of the class plan (and the teacher guide pages) once. No personal data."""
    prompt = '\n\n'.join(part for part in [
        f'Materia: {subject}. Grado de los estudiantes: {Grade(grade).label}.',
        f'Planificación de la sesión:\n{session_text}',
        f'Resumen de las páginas del libro del alumno: {book_summary}' if book_summary else '',
        'Imágenes de la guía del docente a continuación.' if guide_parts else '',
    ] if part)
    data, model = generate_json(LESSON_SYSTEM, [{'text': prompt}, *guide_parts], LESSON_SCHEMA)
    if not data.get('moments'):
        raise AIError('No se encontraron los momentos de la clase en la planificación. Revisa la sesión elegida.')
    return data, model


def adapt_lesson(*, lesson, book_summary, subject, subject_code, grade, condition, adjustments, chosen_grade=''):
    """Adapts the whole class for ONE condition. Receives no personal data."""
    moments = '\n'.join(f'- {m.get("name", "")} ({m.get("duration", "")}): {m.get("activities", "")}'
                         for m in lesson.get('moments', []))
    prompt = [
        f'Materia: {subject}. Grado: {Grade(grade).label}.',
        rules_for(condition, adjustments, subject_code, grade),
        f'Clase: {lesson.get("title", "")}. Destreza: {lesson.get("skill", "")}. '
        f'Indicador: {lesson.get("indicator", "")}. Objetivo: {lesson.get("objective", "")}.',
        f'Momentos de la clase:\n{moments}',
        f'Materiales: {lesson.get("materials", "")}',
        f'Apoyo sugerido por la guía del docente: {lesson.get("guide_support", "")}' if lesson.get('guide_support') else '',
        f'Lo que trabaja el alumno en su libro: {book_summary}' if book_summary else '',
        f'Preguntas de metacognición de la clase: {"; ".join(lesson.get("metacognition", []))}',
        (f'El docente decidió una adaptación de {AdaptationGrade(chosen_grade).label}: aplícala.' if chosen_grade
         else 'Sugiere el grado de adaptación más adecuado y aplícalo.'),
        'Adapta todos los momentos, en el mismo orden. En "evaluation" explica cómo evaluar al estudiante con el '
        'indicador de la clase. En "metacognition" no copies las preguntas de la clase: reescríbelas para este '
        'estudiante, con palabras sencillas y de máximo 10 palabras cada una (por ejemplo "¿Qué te ayudó a '
        'multiplicar?").',
        SPELLING_RULE,
    ]
    data, model = generate_json(LESSON_ADAPTATION_SYSTEM, [{'text': '\n\n'.join(p for p in prompt if p)}],
                                LESSON_ADAPTATION_SCHEMA)
    if not data.get('moments') or not data.get('plan_row'):
        raise AIError('La IA no devolvió la clase adaptada completa. Inténtalo de nuevo.')
    return data, model


SPELLING_RULE = (
    'Revisión final obligatoria de ortografía: un estudiante con NEE aprende a leer con este texto, así que '
    'cada palabra debe llevar sus tildes y signos correctos (célula, núcleo, también, ¿Qué…?, ¡Muy bien!). '
    'Revisa todos los campos antes de responder.'
)

STEP_PREFIX = re.compile(r'^\s*paso\s*\d+\s*[:.\-–]?\s*', re.IGNORECASE)


def _step_title(step):
    """Title without "Paso N"; if nothing is left, the first words of the instruction."""
    title = STEP_PREFIX.sub('', step.get('title', '')).strip()
    if title:
        return title
    words = step.get('instruction', '').split()
    return ' '.join(words[:6]).rstrip('.,:;') + ('…' if len(words) > 6 else '')
MAX_DRAWN_VISUALS = 4


def normalize_adaptation(content, figure_count=0):
    """
    Cleans the model's answer: the templates number the steps themselves (drop "Paso 1:"),
    figure numbers must exist (each figure used once), and drawn visuals are validated (at most four).
    """
    drawn = 0
    used_figures = set()
    for item in content.setdefault('concepts', []) + content.setdefault('steps', []):
        if 'instruction' in item:
            item['title'] = _step_title(item)
            item.setdefault('options', [])
        number = item.get('figure_number') or 0
        valid = 1 <= number <= figure_count and number not in used_figures
        item['figure_number'] = number if valid else 0
        used_figures.add(item['figure_number'])
        visual = None
        if not item['figure_number'] and drawn < MAX_DRAWN_VISUALS:
            visual = visuals.clean_visual(item.get('visual'))
        item['visual'] = visual
        drawn += 1 if visual else 0
    questions = []
    for question in content.get('check_questions', []):
        options = [o for o in question.get('options', []) if str(o).strip()][:4]
        if question.get('question') and len(options) >= 2:
            questions.append({'question': question['question'], 'options': options,
                              'answer': question.get('answer', '')})
    content['check_questions'] = questions[:3]
    return content


def condition_label(condition):
    return Condition(condition).label
