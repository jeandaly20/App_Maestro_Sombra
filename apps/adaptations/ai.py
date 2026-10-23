"""
Google Gemini client (REST API) and the three prompts of the system:

1. analyze_material: reads the worksheet ONCE (page images and/or text) with the multimodal
   model and returns its text, a description and location of every figure and the barriers detected.
2. adapt_worksheet: restructures that content for one SEN condition following the
   UDL rules of udl_rules.py. It only receives text, never the original file again.
3. recommend_next_steps: reads the teacher's classroom evaluation and advises how to adjust
   the next adaptation.

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

from apps.core.choices import Condition, Grade

from . import visuals
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
    },
    'required': ['readable', 'extracted_text', 'figures', 'detected_barriers', 'cognitive_load', 'summary'],
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
        'resume en una frase qué trabaja la guía.'
    )
    parts = [{'text': prompt}]
    if text:
        parts.append({'text': f'Texto de la guía:\n{text}'})
    parts += list(image_parts)
    data, model = generate_json(ANALYSIS_SYSTEM, parts, ANALYSIS_SCHEMA)
    if not data.get('readable', False):
        raise AIError(data.get('problem') or 'No se pudo leer el material. Toma una nueva foto o pega el texto.')
    return data, model


def adapt_worksheet(*, analysis, subject, topic, grade, condition, adjustments, simplify=False, feedback=''):
    """Restructures the analyzed worksheet for ONE condition. Receives no personal data."""
    figures = analysis.get('figures', [])
    figure_lines = '\n'.join(f'Figura {n}: {f["description"]}' for n, f in enumerate(figures, start=1))
    prompt = [
        f'Materia: {subject}. Tema: {topic}. Grado: {Grade(grade).label}.',
        rules_for(condition, adjustments),
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
    content, model = generate_json(ADAPTATION_SYSTEM, [{'text': '\n\n'.join(prompt)}], ADAPTATION_SCHEMA)
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
