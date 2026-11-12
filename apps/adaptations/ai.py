"""
Google Gemini client (REST API) and the three prompts of the system:

1. analyze_material: reads the worksheet ONCE (page images and/or text) with the multimodal
   model and returns its text, a description and location of every figure and the barriers detected.
2. adapt_worksheet: restructures that content for one student, following the UDL rules of
   udl_rules.py for all their SEN conditions. It only receives text, never the original file again.
3. recommend_next_steps: reads the teacher's classroom evaluation and advises how to adjust
   the next adaptation.
4. analyze_lesson / adapt_lesson ("Adaptar clase"): read one session of the teacher's class plan
   (plus the teacher guide pages, if any) once, then adapt the whole class for each student.

Privacy (LOPDP): the model never receives the student's name, notes or birth date;
only the grade, the conditions and the UDL adjustments.
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
from . import nee_modules
from .curriculum import level_note, worksheet_rules
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
        # The answers already solved on the photo (by hand, marked, underlined...): kept apart so they never
        # reach the worksheet. An activity the teacher wrote by hand (a notebook) is NOT here: it is the content.
        'handwritten': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
        'source_kind': {'type': 'STRING', 'enum': ['IMPRESA', 'ESCRITA_A_MANO', 'MIXTA']},
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
        # The worksheet's own title (the teacher no longer types it)
        'title': {'type': 'STRING'},
        # "Adaptar material": whether the worksheet works the topic the teacher wrote
        'matches_topic': {'type': 'BOOLEAN'},
        'topic_mismatch': {'type': 'STRING'},
        # What the worksheet really is, to stop if it does not match the registered subject, and warn about the grade
        'detected_subject': {'type': 'STRING', 'enum': ['MAT', 'LEN', 'CIE', 'SOC', 'ING', 'ART', 'OTRA']},
        'detected_level': {'type': 'STRING', 'enum': ['PREPARATORIA', 'ELEMENTAL', 'MEDIA', 'SUPERIOR', 'BACHILLERATO']},
        # "Adaptar clase" only: whether the pages belong to the chosen session of the class plan
        'matches_lesson': {'type': 'BOOLEAN'},
        'lesson_mismatch': {'type': 'STRING'},
    },
    'required': ['readable', 'extracted_text', 'figures', 'detected_barriers', 'cognitive_load', 'summary',
                 'title', 'detected_subject', 'detected_level'],
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
        'a': {'type': 'INTEGER'},
        'b': {'type': 'INTEGER'},
        'show_products': {'type': 'BOOLEAN'},
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
    'Separa la ACTIVIDAD de lo que YA ESTÁ RESUELTO. La actividad (consignas, preguntas, ejercicios, datos, '
    'textos para leer y tablas por llenar) va en "extracted_text", esté impresa o escrita a mano: la guía puede '
    'venir de un libro, de una hoja impresa, o de un cuaderno o pizarra donde el docente la escribió a mano, y '
    'entonces lo escrito a mano ES la actividad. Lo que un estudiante ya resolvió no es parte de la actividad: '
    'resultados escritos, espacios o tablas llenados, opciones marcadas, palabras subrayadas, encerradas o '
    'unidas con líneas, dibujos o colores hechos como respuesta y tachones. Anota cada respuesta en "handwritten" '
    '(por ejemplo "99" o "55 x 55 = 3025" en una operación, "perro" escrito en un espacio, "opción b marcada" o '
    '"Quito subrayado") y extrae la actividad como si estuviera en blanco: espacios, tablas y opciones vacíos. '
    'Si nada está resuelto, como en un libro nuevo, "handwritten" queda vacío. En "source_kind" indica si la '
    'actividad está IMPRESA, ESCRITA_A_MANO o MIXTA. Señala además las barreras '
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

# The same for every subject and condition; the subject (curriculum.worksheet_rules) and the student's
# conditions (udl_rules + nee_modules) add their own parts
PEDAGOGY_RULES = (
    'Las respuestas que ya estaban resueltas en la foto (la lista "Respuestas ya escritas") son de otro '
    'estudiante: nunca las uses, menciones ni des como pista; la actividad empieza en blanco.\n'
    'Estructura obligatoria de la guía adaptada:\n'
    '- "learning_goal": una frase corta que empiece con "Vas a aprender…".\n'
    '- "concepts" (APRENDE): de 2 a 5 conceptos esenciales que el estudiante necesita para hacer la tarea, '
    'elegidos según los objetivos de la guía. Para cada uno: "explanation" en 2 o 3 frases cortas y sencillas '
    'que expliquen qué es y para qué sirve (no qué muestra una imagen), y "example" con una comparación de la '
    'vida diaria del estudiante. Si una figura de la guía ayuda a entender ese concepto, pon su número en '
    '"figure_number". Si la guía trae un modelo resuelto, inclúyelo paso a paso en el "example" del concepto que '
    'explica: es lo que el estudiante va a imitar.\n'
    '- "steps" (AHORA PRACTICA): las actividades y preguntas de la guía original, adaptadas. Cada paso dice qué '
    'hacer y cómo hacerlo, con una "hint" (pista) que remite a lo aprendido en "concepts" y da una estrategia, '
    'no la respuesta. La "keyword" es la palabra clave de la consigna (un verbo o un concepto), nunca la '
    'respuesta. Ningún paso, pista, título, opción o dibujo puede revelar la respuesta de ese paso ni de otro. '
    'Las opciones de un paso son del mismo tema. Si la guía no trae actividades, crea de 2 a 4 actividades '
    'cortas para practicar su contenido (completar, unir, elegir o clasificar), sin datos nuevos.\n'
    '- "check_questions" (COMPRUEBA LO QUE APRENDISTE): de 2 a 3 preguntas de opción múltiple con 3 opciones y '
    'su "answer" correcta, sobre lo que se practicó. Las opciones incorrectas son errores comunes y creíbles del '
    'tema, nunca absurdas. Cada opción es solo la respuesta (una palabra, un número o una frase corta), sin '
    'repetir la pregunta.\n'
    '- Nunca escribas una instrucción que solo diga "Observa la figura" o que describa la imagen: la '
    'descripción para accesibilidad va únicamente en "figure_description".'
)

IMAGE_RULES = (
    'Imágenes: si un concepto o un paso usa una figura de la guía original, escribe su número en "figure_number" '
    '(0 si ninguna); usa cada figura una sola vez y solo si ayuda a entender. Si una actividad necesita un '
    'material de la guía para hacerse (una tabla para completar, una sopa de letras, un mapa, una plantilla, un '
    'dibujo para colorear o señalar), ese paso DEBE llevar el "figure_number" de esa figura. Si el texto menciona '
    'un dibujo, imagen o figura que NO está en la lista de figuras, no le pidas al estudiante que lo mire: '
    'reescribe la consigna para que se entienda sin ese dibujo. Si un concepto o paso necesita una ilustración que '
    'la guía no tiene, puedes usar en "visual" el tipo SEQUENCE (items: de 2 a 5 frases muy cortas con el orden '
    'de la actividad); si ninguno sirve, usa NONE. Máximo 5 pasos con "visual", y nunca uno que contradiga la guía.'
)

# Only for Mathematics: the drawings the system can make are all of numbers
MATH_VISUALS = (
    'Dibujos de números (solo con datos de la guía): FRACTION_CIRCLE o FRACTION_BAR (parts, shaded y crossed para '
    'lo que se quita o se comparte), NUMBER_LINE (start, end y marks), COUNT (count y crossed) o MULTIPLICATION_GRID '
    '(a y b de 2 cifras, show_products=true solo en APRENDE, para el ejemplo resuelto que trae la guía, y false en '
    'AHORA PRACTICA, para la cuadrícula vacía que el estudiante completa; es la tabla de doble entrada que '
    'descompone en decenas y unidades). Si la figura de la guía es una tabla o cuadrícula que el sistema puede '
    'dibujar, usa el "visual" en su lugar (figure_number 0): se ve completa y nítida.'
)
NON_MATH_VISUALS = {'SEQUENCE', 'NONE'}


def image_rules(subject_code):
    return IMAGE_RULES + ('\n' + MATH_VISUALS if subject_code == 'MAT' else '')


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


# Google overloads one model at a time (503), and its spikes often last seconds: each model gets a second try
# after a short wait, and then the next model of the chain, which has its own capacity. A model that does not
# answer in time is skipped for a minute, so the next calls of the same job do not wait for it again.
RETRY_WAIT_SECONDS = 3
PAUSE_SECONDS = 60
_paused_until = {}

OVERLOADED = ('Los servidores de la IA de Google (Gemini) están saturados en este momento. No es un problema de '
              'tu material ni de la aplicación: vuelve a intentarlo en unos minutos.')
QUOTA = ('Se alcanzó el límite de uso de la IA de Google (cuota de Gemini). Espera un minuto y vuelve a intentarlo; '
         'si se repite, se agotó la cuota del día o hay que revisar el plan de Gemini.')
NO_CONNECTION = 'No hay conexión con la IA de Google. Revisa la conexión a internet y vuelve a intentarlo.'


def _is_timeout(error):
    return isinstance(error, TimeoutError) or isinstance(getattr(error, 'reason', None), TimeoutError)


def model_chain():
    """The main model and then its fallbacks, without repeats; the ones that just timed out go last."""
    chain = list(dict.fromkeys([settings.GEMINI_MODEL, *settings.GEMINI_FALLBACK_MODELS]))
    now = time.monotonic()
    return sorted(chain, key=lambda model: _paused_until.get(model, 0) > now)


def generate_json(system, parts, schema):
    """
    Calls Gemini and returns (data, model_version). Each model of the chain gets two tries (the last one, one),
    so an overloaded model is replaced by the next one in seconds. The teacher's message says what really
    happened: Google overloaded, quota used up or no internet.
    """
    if not settings.GEMINI_API_KEY:
        raise AIError('Falta configurar la clave de la API de Gemini (GEMINI_API_KEY en el archivo .env).')
    last_error = None
    statuses = []
    chain = model_chain()
    for position, model in enumerate(chain):
        attempts = 1 if position == len(chain) - 1 else 2
        for attempt in range(attempts):
            if attempt:
                time.sleep(RETRY_WAIT_SECONDS)
            try:
                answer = _call(model, system, parts, schema, settings.GEMINI_TIMEOUT_SECONDS)
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
                statuses.append(error.code)
                logger.warning('Gemini HTTP %s with model %s (attempt %s)', error.code, model, attempt + 1)
                if error.code == 404:
                    break  # this model is not offered to this key (retired or renamed): try the next one
                if error.code not in RETRYABLE_STATUS:
                    raise AIError('La IA rechazó la solicitud. Revisa la clave de Gemini o el archivo enviado.') from error
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
                statuses.append('timeout' if _is_timeout(error) else 'offline')
                logger.warning('Gemini connection error with model %s: %s', model, error)
                break  # no answer in time (or no internet): retrying the same model would only add waiting
            except (KeyError, IndexError, json.JSONDecodeError) as error:
                last_error = error
                logger.warning('Gemini returned an unexpected answer with model %s: %s', model, repr(error)[:200])
        # This model failed (overloaded, too slow or retired): for a minute the next calls of the same class or
        # worksheet try the other models first instead of losing seconds on it again
        if statuses and statuses[-1] != 'offline':
            _paused_until[model] = time.monotonic() + PAUSE_SECONDS
    if statuses and all(status == 'offline' for status in statuses):
        message = NO_CONNECTION
    elif statuses and all(status == 404 for status in statuses):
        message = ('Los modelos de IA configurados ya no están disponibles en Google. Revisa GEMINI_MODEL y '
                   'GEMINI_FALLBACK_MODELS en el archivo .env.')
    elif 429 in statuses and not ({500, 503, 'timeout'} & set(statuses)):
        message = QUOTA
    elif statuses:
        message = OVERLOADED
    else:
        message = 'La IA no devolvió una respuesta válida. Inténtalo de nuevo en unos minutos.'
    raise AIError(message) from last_error


TOPIC_MISMATCH = 'Las páginas no corresponden a la clase elegida'
MATERIAL_MISMATCH = 'El material no coincide con lo que escribiste'


def topic_note(session_text, images, ok_field, why_field):
    """Asks the model to check that some images (the book pages, the guide) belong to the chosen session."""
    return (
        f'Esta es la clase (sesión) de la planificación que el docente eligió:\n{session_text[:1500]}\n'
        f'Indica en "{ok_field}" si {images} corresponden a esa clase (true) o no (false). Compara con el '
        'objetivo específico y las actividades de la sesión, no solo con la materia, el eje o la destreza '
        'general (muchas planificaciones repiten la misma destreza en todas las sesiones). Responde false si '
        'trabajan otro contenido, aunque sea de la misma materia: por ejemplo, la sesión trata el perímetro y '
        'las imágenes, las fracciones, o la sesión trata el sustantivo y las imágenes, los verbos. Responde true si '
        'corresponden a esas actividades, aunque usen otras palabras. Si es false, explica en '
        f'"{why_field}", en una frase corta para el docente, de qué tratan las imágenes y de qué trata la clase.'
    )


def analyze_material(*, text='', image_parts=(), subject, topic, session_text='', check_topic=False):
    """
    Reads the worksheet once: its page images (photo, PDF pages, Word pictures) and/or its text.
    With session_text ("Adaptar clase") it also checks that the pages belong to that session; with check_topic
    ("Adaptar material") that the worksheet works the topic the teacher wrote.
    """
    prompt = (
        f'Materia: {subject}. Tema: {topic}.\n'
        'Analiza esta guía escolar: extrae su contenido, describe y ubica cada figura, y señala las barreras '
        'de aprendizaje, el número de ejercicios y la carga cognitiva (BAJA, MEDIA o ALTA). En "summary" '
        'resume en una frase qué trabaja la guía y en "title" escribe el título de la guía (el que trae '
        'impreso o, si no tiene, uno corto que diga su tema). Según su contenido (no según lo que dice el docente), '
        'indica en "detected_subject" la materia que realmente trabaja (MAT Matemática, LEN Lengua y Literatura, '
        'CIE Ciencias Naturales, SOC Estudios Sociales, ING Inglés, ART Educación Cultural y Artística u OTRA) y '
        'en "detected_level" el subnivel del currículo ecuatoriano para el que está pensada: PREPARATORIA (1.º), '
        'ELEMENTAL (2.º a 4.º), MEDIA (5.º a 7.º), SUPERIOR (8.º a 10.º) o BACHILLERATO.'
    )
    if session_text:
        prompt += '\n' + topic_note(session_text, 'las páginas', 'matches_lesson', 'lesson_mismatch')
    elif check_topic:
        prompt += (
            f'\nEl docente dice que esta guía trabaja el tema "{topic}". Indica en "matches_topic" si su contenido '
            'trabaja ese tema (true) o no (false). Responde true si es el mismo tema aunque use otras palabras, o '
            'si es una parte de ese tema; false si trata otro contenido, aunque sea de la misma materia (por '
            'ejemplo, el tema dice fracciones y la guía trabaja la multiplicación, o el tema dice el sustantivo y '
            'la guía trabaja los verbos). Si es false, explica en '
            '"topic_mismatch", en una frase corta para el docente, de qué trata la guía.'
        )
    parts = [{'text': prompt}]
    if text:
        parts.append({'text': f'Texto de la guía:\n{text}'})
    parts += list(image_parts)
    data, model = generate_json(ANALYSIS_SYSTEM, parts, ANALYSIS_SCHEMA)
    if not data.get('readable', False):
        raise AIError(data.get('problem') or 'No se pudo leer el material. Toma una nueva foto o pega el texto.')
    return data, model


def adapt_worksheet(*, analysis, subject, topic, grade, conditions, adjustments, simplify=False, feedback='',
                    subject_code=None):
    """
    Restructures the analyzed worksheet for ONE student and all their conditions. No personal data.
    The answer must carry the fields of each condition (nee_modules); if the checks in code find something
    missing (a step without its keyword, sentences too long for the student...), the AI is asked once to
    correct exactly that. What is still missing is kept in "nee_check" for the teacher's review.
    """
    figures = analysis.get('figures', [])
    figure_lines = '\n'.join(f'Figura {n}: {f["description"]}' for n, f in enumerate(figures, start=1))
    # What was already solved on the photo: the AI is told not to use it, and the code drops any step that
    # still carries one of those numbers (normalize_adaptation)
    solved = [item for item in (analysis.get('handwritten') or []) if str(item).strip()]
    forbidden = handwritten_numbers(analysis)
    prompt = [
        f'Materia: {subject}. Tema: {topic}. Grado: {Grade(grade).label}.',
        rules_for(conditions, adjustments, subject_code, grade),
        level_note(analysis, grade),
        f'Contenido de la guía original:\n{analysis["extracted_text"]}',
        f'Figuras de la guía original:\n{figure_lines or "(la guía no tiene figuras)"}',
        'Barreras detectadas: ' + '; '.join(analysis.get('detected_barriers', [])),
        (f'Respuestas ya escritas en la foto por otro estudiante, que no debes usar ni dar como pista: '
         f'{"; ".join(str(item) for item in solved[:20])}.' if solved else ''),
        PEDAGOGY_RULES,
        worksheet_rules(subject_code),
        image_rules(subject_code),
        nee_modules.instructions_for(conditions),
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
    text = '\n\n'.join(part for part in prompt if part)
    schema = nee_modules.schema_for(ADAPTATION_SCHEMA, conditions)

    def ask(extra=''):
        content, model = generate_json(ADAPTATION_SYSTEM, [{'text': text + extra}], schema)
        content = normalize_adaptation(content, figure_count=len(figures), forbidden=forbidden,
                                       subject_code=subject_code)
        return content, model, nee_modules.problems(content, conditions)

    content, model, missing = ask()
    if missing:
        logger.info('Worksheet misses its condition rules, asking once to correct: %s', missing)
        # The spelling rule goes last again: a correction must not cost the accents
        correction = ('\n\nTu respuesta anterior no cumplió estas reglas obligatorias de las condiciones del '
                      'estudiante. Devuelve la guía completa otra vez, corrigiendo exactamente esto:\n'
                      + '\n'.join(f'- {item}' for item in missing) + '\n\n' + SPELLING_RULE)
        try:
            second, second_model, second_missing = ask(correction)
            if len(second_missing) <= len(missing):
                content, model, missing = second, second_model, second_missing
        except AIError:
            # Google overloaded right then: the first version is kept, with its notes for the teacher
            logger.warning('The correction call failed; the first version of the worksheet is kept')
    content['nee_check'] = {'passed': not missing, 'problems': missing}
    content['total_minutes'] = nee_modules.total_minutes(content)
    return content, model


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


def recommend_next_steps(*, subject, topic, grade, conditions, adjustments, summary, evaluation):
    """Pedagogical advice from the teacher's classroom evaluation. Receives no personal data."""
    prompt = '\n'.join([
        f'Materia: {subject}. Tema: {topic}. Grado: {Grade(grade).label}. Condiciones: {conditions_label(conditions)}.',
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
        'guide_matches': {'type': 'BOOLEAN'},
        'guide_mismatch': {'type': 'STRING'},
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


WEEK_NOTE = (
    'Esta planificación es una SEMANA completa: sus sesiones son los días de clase del mismo tema. Incluye los '
    'momentos de TODAS las sesiones, en orden; si hay más de una, empieza el nombre de cada momento con su sesión '
    '(por ejemplo "Sesión 30 · Calentemos", "Sesión 31 · Calentemos").'
)


def analyze_lesson(*, session_text, guide_parts=(), book_summary='', subject, grade, check_guide=False,
                   whole_week=False):
    """Reads one session (or a whole week) of the class plan and the teacher guide pages once. No personal data."""
    prompt = '\n\n'.join(part for part in [
        f'Materia: {subject}. Grado de los estudiantes: {Grade(grade).label}.',
        WEEK_NOTE if whole_week else '',
        f'Planificación de la sesión:\n{session_text}',
        f'Resumen de las páginas del libro del alumno: {book_summary}' if book_summary else '',
        'Imágenes de la guía del docente a continuación.' if guide_parts else '',
        topic_note(session_text, 'las imágenes de la guía del docente', 'guide_matches', 'guide_mismatch')
        if guide_parts and check_guide else '',
    ] if part)
    data, model = generate_json(LESSON_SYSTEM, [{'text': prompt}, *guide_parts], LESSON_SCHEMA)
    if not data.get('moments'):
        raise AIError('No se encontraron los momentos de la clase en la planificación. Revisa la sesión elegida.')
    return data, model


def adapt_lesson(*, lesson, book_summary, subject, subject_code, grade, conditions, adjustments, chosen_grade='',
                 whole_week=False):
    """Adapts the whole class (or week) for ONE student and all their conditions. Receives no personal data."""
    moments = '\n'.join(f'- {m.get("name", "")} ({m.get("duration", "")}): {m.get("activities", "")}'
                         for m in lesson.get('moments', []))
    prompt = [
        f'Materia: {subject}. Grado: {Grade(grade).label}.',
        rules_for(conditions, adjustments, subject_code, grade),
        f'Clase: {lesson.get("title", "")}. Destreza: {lesson.get("skill", "")}. '
        f'Indicador: {lesson.get("indicator", "")}. Objetivo: {lesson.get("objective", "")}.',
        f'Momentos de la clase:\n{moments}',
        f'Materiales: {lesson.get("materials", "")}',
        f'Apoyo sugerido por la guía del docente: {lesson.get("guide_support", "")}' if lesson.get('guide_support') else '',
        f'Lo que trabaja el alumno en su libro: {book_summary}' if book_summary else '',
        f'Preguntas de metacognición de la clase: {"; ".join(lesson.get("metacognition", []))}',
        (f'El docente decidió una adaptación de {AdaptationGrade(chosen_grade).label}: aplícala.' if chosen_grade
         else 'Sugiere el grado de adaptación más adecuado y aplícalo.'),
        ('Es una semana completa: adapta los momentos de todas sus sesiones y conserva en el nombre de cada '
         'momento la sesión a la que pertenece.' if whole_week else ''),
        'Adapta todos los momentos, en el mismo orden. En "evaluation" explica cómo evaluar al estudiante con el '
        'indicador de la clase. En "metacognition" no copies las preguntas de la clase: reescríbelas para este '
        'estudiante, con palabras sencillas y de máximo 10 palabras cada una (por ejemplo "¿Qué te ayudó a '
        'entender el tema?").',
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

# "Paso 1:", "Paso a:", "a)", "1." and their combinations ("Paso 1: Paso a: …")
STEP_PREFIX = re.compile(r'^\s*(?:(?:paso\s*(?:\d+|[a-z])\b|\d+|[a-z])\s*[:.)\-–]\s*)+', re.IGNORECASE)
ANSWER_LIKE = re.compile(r'^[\d\s.,×x+=\-]+$')
TWO_DIGIT_PRODUCT = re.compile(r'\b(\d{2})\s*(?:×|x|por|multiplicado por)\s*(\d{2})\b', re.IGNORECASE)


NUMBER = re.compile(r'\d+')


def _numbers(text):
    # "2 025" and "2025" are the same number
    return set(NUMBER.findall(re.sub(r'\b(\d{1,3})[ .](\d{3})\b', r'\1\2', str(text))))


def handwritten_numbers(analysis):
    """
    Numbers another student wrote on the photo (usually the answers), minus the printed ones
    ("Calcula 55 × 55", "20 + 25 = 45"), which the student may have copied by hand.
    """
    printed = _numbers(analysis.get('extracted_text', ''))
    found = _numbers(' '.join(analysis.get('handwritten') or []))
    return {n for n in found if len(n) >= 2} - printed


def _step_title(step):
    """Title without "Paso N"; if nothing is left, the first words of the instruction."""
    title = STEP_PREFIX.sub('', step.get('title', '')).strip()
    if title:
        return title
    words = step.get('instruction', '').split()
    return ' '.join(words[:6]).rstrip('.,:;') + ('…' if len(words) > 6 else '')
MAX_DRAWN_VISUALS = 6


def normalize_adaptation(content, figure_count=0, forbidden=(), subject_code=None):
    """
    Cleans the model's answer: the templates number the steps themselves (drop "Paso 1:"), a keyword
    is never the answer, figure numbers must exist (each figure used once) and drawn visuals are
    validated. A drawn multiplication grid replaces a photo of the same grid: it is always complete.
    Steps and questions built on what another student wrote by hand (forbidden) are dropped.
    """
    def reveals(*texts):
        return bool(forbidden) and bool(_numbers(' '.join(str(t) for t in texts)) & set(forbidden))

    content['steps'] = [step for step in content.get('steps', [])
                        if not reveals(step.get('title', ''), step.get('instruction', ''), *step.get('options', []))]
    for step in content['steps']:
        if reveals(step.get('hint', '')):
            step['hint'] = ''
    content['check_questions'] = [q for q in content.get('check_questions', [])
                                  if not reveals(q.get('question', ''), *q.get('options', []))]
    drawn = 0
    used_figures = set()
    for item in content.setdefault('concepts', []) + content.setdefault('steps', []):
        if 'instruction' in item:
            item['title'] = _step_title(item)
            item.setdefault('options', [])
            if ANSWER_LIKE.match(str(item.get('keyword', '')).strip() or 'x'):
                item['keyword'] = ''  # a number or an operation would give the answer away
        visual = visuals.clean_visual(item.get('visual')) if drawn < MAX_DRAWN_VISUALS else None
        if visual and subject_code != 'MAT' and visual['type'] not in NON_MATH_VISUALS:
            visual = None  # a drawing of numbers does not belong in a reading, science or arts worksheet
        if visual and visual['type'] == 'MULTIPLICATION_GRID':
            if 'instruction' in item:
                visual['show_products'] = False  # a practice grid is for the student to fill in, never solved
            # The grid shows the same product the text asks for (concept: its example; step: its instruction)
            if product := TWO_DIGIT_PRODUCT.search(str(item.get('instruction', item.get('example', '')))):
                visual['a'], visual['b'] = int(product[1]), int(product[2])
            if {str(visual['a']), str(visual['b'])} & set(forbidden):
                visual = None
        number = item.get('figure_number') or 0
        if visual and visual['type'] == 'MULTIPLICATION_GRID':
            number = 0
        valid = 1 <= number <= figure_count and number not in used_figures
        item['figure_number'] = number if valid else 0
        used_figures.add(item['figure_number'])
        item['visual'] = visual if not item['figure_number'] else None
        drawn += 1 if item['visual'] else 0
    questions = []
    for question in content.get('check_questions', []):
        options = [o for o in question.get('options', []) if str(o).strip()][:4]
        if question.get('question') and len(options) >= 2:
            questions.append({'question': question['question'], 'options': options,
                              'answer': question.get('answer', '')})
    content['check_questions'] = questions[:3]
    return content


def conditions_label(conditions):
    return ' + '.join(Condition(value).label for value in conditions)
