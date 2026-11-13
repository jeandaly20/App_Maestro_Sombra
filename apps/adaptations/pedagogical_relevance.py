"""
Pedagogical relevance engine: relates the student's SEN (NEE), their individual pedagogical profile, the subject
and what the activity really asks for, BEFORE an adaptation is generated. Deterministic, run in this server.

What it decides (rules in code):
- which demands the activity makes (reading, writing, calculation, sustained attention...), from its own text;
- which of the student's NEE apply to THIS activity (dyscalculia does not apply to a reading without numbers);
- which observed barriers, strengths and individual supports of the profile are relevant to it, each with a reason;
- which DUA adjustments are suggested and why; and what information is missing.
It never diagnoses, never adds conditions, never assumes a barrier affects every subject, and never states that an
adaptation will work: the teacher reviews it.

What the AI receives (privacy, LOPDP): by default only what derives from the NEE, the subject and the activity
(`ai_prompt`); the profile is analysed here. It is added to the request (`share_profile=True`) only when
students.models.ai_profile_allowed() says so: the system setting turned on AND an individual authorization in
force covering it. Even then only fixed category labels are sent (`minimized_profile`), never the teacher's text.

review_checks() are the automatic checks of the AI's answer before it is saved: they ask for the teacher's review,
they never mark an adaptation as "pedagogically validated".
"""
import re
import unicodedata

from apps.core.choices import Condition, UdlAdjustment

PRIVACY_NOTE = ('El perfil pedagógico se analizó en el servidor y no se envió a la IA: no hay una autorización '
                'individual vigente que cubra ese envío, o está deshabilitado en el sistema. Aplica los apoyos '
                'individuales al revisar la adaptación.')
SHARED_NOTE = ('Con la autorización vigente del representante, se enviaron a la IA solo categorías del perfil '
               '(nunca el texto escrito). Revisa cómo se aplicaron.')


def plain(text):
    text = unicodedata.normalize('NFKD', str(text or ''))
    return ''.join(c for c in text if not unicodedata.combining(c)).lower()


def found(text, words):
    return [word for word in words if word in text]


# ---------- Fixed vocabulary: the teacher writes freely, the engine maps it to these categories ----------

DEMANDS = {
    'lectura': 'la lectura', 'escritura': 'la escritura', 'calculo': 'el cálculo y las cantidades',
    'atencion': 'la atención sostenida', 'comprension': 'la comprensión de consignas', 'memoria': 'la memoria de trabajo',
    'organizacion': 'la organización del tiempo', 'comunicacion': 'la comunicación y el trabajo con otros',
    'sensorial': 'los estímulos sensoriales', 'cambios': 'las transiciones entre momentos', 'motricidad': 'la motricidad fina',
}

BARRIER_WORDS = {
    'lectura': ['leer', 'lectura', 'texto', 'letras', 'deletre', 'silab', 'decodific', 'palabras largas'],
    'escritura': ['escrib', 'escritura', 'ortograf', 'copiar', 'redact'],
    'calculo': ['cantidad', 'numero', 'calculo', 'operacion', 'sumar', 'restar', 'multiplic', 'dividir', 'matemat',
                'contar'],
    'atencion': ['atencion', 'distrae', 'concentr', 'dispersa', 'impulsiv', 'quieto', 'inquiet'],
    'comprension': ['instruccion', 'consigna', 'comprend', 'entender', 'abstract'],
    'memoria': ['memoria', 'recordar', 'olvida', 'retener'],
    'organizacion': ['organiz', 'planific', 'tiempo', 'terminar', 'termina las'],
    'comunicacion': ['interacc', 'comunic', 'en grupo', 'companeros', 'expresar', 'hablar'],
    'sensorial': ['ruido', 'luz', 'sensorial', 'sonido', 'tacto'],
    'cambios': ['cambio', 'rutina', 'imprevist', 'transicion'],
    'motricidad': ['motric', 'recortar', 'pinza', 'trazar', 'trazo'],
}

STRENGTHS = {
    'oral': ('comprensión oral', ['oral', 'escuchar', 'escucha', 'explicaciones', 'conversa', 'verbal']),
    'visual': ('apoyo visual', ['imagen', 'imagenes', 'visual', 'dibuj', 'grafic', 'colores']),
    'manipulativo': ('trabajo práctico y manipulativo', ['practic', 'manipul', 'concreto', 'con las manos', 'material']),
    'memoria': ('buena memoria', ['memoria', 'recuerda', 'memoriza']),
    'interes': ('intereses personales', ['le gusta', 'interes', 'motiva', 'disfruta', 'favorit']),
    'social': ('participación y trabajo con otros', ['grupo', 'companeros', 'colabora', 'participa']),
    'creatividad': ('creatividad', ['crea', 'imagin', 'arte', 'musica', 'canta']),
    'tecnologia': ('uso de tecnología', ['computador', 'tablet', 'tecnolog', 'celular']),
}

SUPPORTS = {
    'instrucciones_breves': ('instrucciones breves o segmentadas', ['breve', 'corta', 'una consigna', 'paso a paso',
                                                                   'segment', 'una instruccion'],
                             {'comprension', 'atencion', 'lectura'}),
    'lectura_acompanada': ('lectura acompañada', ['lectura acompanada', 'voz alta', 'leerle', 'lectura en voz'],
                           {'lectura'}),
    'apoyos_visuales': ('apoyos visuales', ['visual', 'imagen', 'pictogram', 'grafic', 'esquema'],
                        {'comprension', 'lectura', 'calculo', 'memoria'}),
    'pausas': ('pausas programadas', ['pausa', 'descanso', 'movimiento', 'pausas activas'], {'atencion', 'sensorial'}),
    # Only where there are quantities: in a reading it would bring a mathematical adaptation that is not needed
    'material_concreto': ('material concreto', ['concreto', 'manipul', 'bloques', 'fichas', 'regleta', 'abaco'],
                          {'calculo'}),
    'tiempo_extra': ('tiempo adicional', ['tiempo adicional', 'mas tiempo', 'tiempo extra'],
                     {'lectura', 'escritura', 'calculo', 'organizacion'}),
    'ubicacion': ('ubicación en el aula', ['ubicar', 'cerca del docente', 'primera fila', 'lugar tranquilo'],
                  {'atencion', 'sensorial'}),
    'anticipacion': ('anticipación de la rutina', ['anticip', 'agenda', 'rutina', 'avisar'], {'cambios', 'organizacion'}),
    'refuerzo': ('refuerzo positivo', ['refuerzo', 'elogi', 'felicit', 'motivacion'], set(DEMANDS)),
    'tecnologia': ('apoyo tecnológico', ['audio', 'computador', 'tablet', 'calculadora'], {'lectura', 'escritura', 'calculo'}),
}

# What each NEE usually affects; whether it applies is decided against what the activity asks for
CONDITION_DEMANDS = {
    Condition.DYSLEXIA: {'lectura', 'escritura'},
    Condition.DYSCALCULIA: {'calculo'},
    Condition.ADHD: {'atencion', 'organizacion'},
    Condition.ASD: {'comunicacion', 'cambios', 'sensorial', 'comprension'},
    Condition.ID_MILD: {'comprension', 'memoria', 'lectura', 'calculo'},
    Condition.ID_MODERATE: {'comprension', 'memoria', 'lectura', 'calculo'},
}

ADJUSTMENT_DEMANDS = {
    UdlAdjustment.ACCESSIBLE_FONT: {'lectura'}, UdlAdjustment.SEGMENTATION: {'comprension', 'atencion', 'lectura'},
    UdlAdjustment.VISUAL_SUPPORT: {'comprension', 'calculo', 'memoria'}, UdlAdjustment.STEP_SEQUENCE: {'organizacion', 'atencion', 'cambios'},
    UdlAdjustment.EASY_READ: {'lectura', 'comprension'}, UdlAdjustment.FEWER_DISTRACTIONS: {'atencion', 'sensorial'},
    UdlAdjustment.HIGH_CONTRAST: {'lectura'},
}

DIGIT_OPERATION = re.compile(r'\d+\s*(?:[x×+\-÷/:]|por|mas|menos)\s*\d+')


# ---------- What the activity asks for ----------

def activity_demands(text, *, exercise_count=0, is_lesson=False, has_figures=False, subject_code=None):
    """{demand: evidence} for the activity, from its own text. Only what the activity shows, nothing assumed."""
    t = plain(text)
    demands = {}
    if len(t) > 600 or found(t, ['lee ', 'lean', 'lectura', 'texto', 'cuento', 'parrafo']):
        demands['lectura'] = 'tiene textos o consignas para leer'
    if found(t, ['escribe', 'escriban', 'redacta', 'completa', 'copia', 'oracion']):
        demands['escritura'] = 'pide escribir o completar'
    if DIGIT_OPERATION.search(t) or found(t, ['suma', 'resta', 'multiplic', 'divid', 'calcula', 'cuanto', 'cuantos',
                                             'perimetro', 'fraccion', 'reparte']):
        demands['calculo'] = 'tiene números u operaciones'
    if exercise_count >= 4 or is_lesson:
        demands['atencion'] = (f'tiene {exercise_count} ejercicios seguidos' if not is_lesson
                               else 'es una clase con varios momentos')
    if found(t, ['explica', 'por que', 'compara', 'clasifica', 'ordena', 'relaciona', 'deduce']):
        demands['comprension'] = 'pide explicar, comparar o clasificar'
    if found(t, ['recuerda', 'memoriza', 'repasa']):
        demands['memoria'] = 'pide recordar contenidos'
    if exercise_count >= 5 or is_lesson:
        demands['organizacion'] = 'requiere organizar varias tareas en el tiempo'
    if found(t, ['conversa', 'expon', 'en grupo', 'parejas', 'dialog', 'presenta', 'pregunta a']):
        demands['comunicacion'] = 'incluye trabajo oral o con otros'
    if found(t, ['dibuja', 'recorta', 'pinta', 'traza', 'colorea', 'une con']):
        demands['motricidad'] = 'pide dibujar, pintar o recortar'
    if found(t, ['musica', 'cancion', 'sonido', 'ritmo', 'palmas']):
        demands['sensorial'] = 'incluye música o sonidos'
    if is_lesson:
        demands['cambios'] = 'tiene transiciones entre momentos de la clase'
    if has_figures and 'comprension' not in demands:
        demands['comprension'] = 'usa figuras que hay que interpretar'
    return demands


# ---------- The engine ----------

def _categories(text, vocabulary):
    t = plain(text)
    return [key for key, words in vocabulary.items() if found(t, words)]


def _items(text):
    """The teacher's text split into its items (sentences, commas or 'y' lists)."""
    return [part.strip() for part in re.split(r'[.;\n]|,|\by\b', str(text or '')) if len(part.strip()) > 3]


def analyze(*, conditions, profile, subject_code, subject_name, topic, activity_text, exercise_count=0,
            has_figures=False, is_lesson=False, objective='', skill='', adjustments=(), share_profile=False):
    """
    The relevance of the student's NEE and profile for this activity. `profile` is a dict with
    learning_strengths, learning_barriers and pedagogical_supports (free text, read only here).
    Returns a dict that never contains the teacher's free text: only categories, labels and reasons.
    """
    profile = profile or {}
    demands = activity_demands(activity_text, exercise_count=exercise_count, is_lesson=is_lesson,
                               has_figures=has_figures, subject_code=subject_code)
    demand_names = set(demands)

    nee = []
    for value in conditions:
        condition = Condition(value)
        touched = sorted(CONDITION_DEMANDS[condition] & demand_names)
        if touched:
            nee.append({'condition': condition.value, 'label': condition.label, 'applies': True,
                        'reason': 'La actividad ' + '; '.join(demands[d] for d in touched) +
                                  f' ({", ".join(DEMANDS[d] for d in touched)}).'})
        else:
            nee.append({'condition': condition.value, 'label': condition.label, 'applies': False,
                        'reason': f'La actividad no exige {", ".join(DEMANDS[d] for d in sorted(CONDITION_DEMANDS[condition]))}'
                                  ': sus reglas no cambian el contenido de esta actividad.'})

    barrier_categories = _categories(profile.get('learning_barriers'), BARRIER_WORDS)
    relevant_barriers = [{'category': c, 'label': DEMANDS[c], 'reason': f'La actividad {demands[c]}.'}
                         for c in barrier_categories if c in demand_names]
    other_barriers = [{'category': c, 'label': DEMANDS[c],
                       'reason': f'Esta actividad no exige {DEMANDS[c]}: no se asume que esa barrera la afecte.'}
                      for c in barrier_categories if c not in demand_names]

    strengths = []
    for key in _categories(profile.get('learning_strengths'), {k: v[1] for k, v in STRENGTHS.items()}):
        label = STRENGTHS[key][0]
        usable = key in ('oral', 'visual', 'interes') or (
            key == 'manipulativo' and demand_names & {'calculo', 'motricidad'}) or (
            key == 'social' and 'comunicacion' in demand_names) or (
            key == 'creatividad' and (subject_code == 'ART' or 'motricidad' in demand_names)) or (
            key == 'memoria' and demand_names & {'memoria', 'calculo'}) or key == 'tecnologia'
        if usable:
            strengths.append({'category': key, 'label': label,
                              'reason': f'Puede usarse como punto de partida: {label} para acceder al contenido de {topic or subject_name}.'})

    supports, unclassified = [], 0
    for item in _items(profile.get('pedagogical_supports')):
        keys = _categories(item, {k: v[1] for k, v in SUPPORTS.items()})
        if not keys:
            unclassified += 1
        for key in keys:
            label, _words, targets = SUPPORTS[key]
            touched = sorted(targets & demand_names)
            if touched and not any(s['category'] == key for s in supports):
                supports.append({'category': key, 'label': label,
                                 'reason': f'Pertinente: la actividad exige {", ".join(DEMANDS[d] for d in touched[:2])}.'})

    udl = []
    for value in adjustments:
        adjustment = UdlAdjustment(value)
        touched = sorted(ADJUSTMENT_DEMANDS.get(adjustment, set()) & demand_names)
        udl.append({'adjustment': adjustment.value, 'label': adjustment.label, 'pertinent': bool(touched),
                    'reason': (f'Sugerido: la actividad exige {", ".join(DEMANDS[d] for d in touched[:2])}.' if touched
                               else 'Sugerencia general de su grupo de NEE; en esta actividad su aporte es limitado.')})

    written = {key: bool((profile.get(key) or '').strip())
               for key in ('learning_strengths', 'learning_barriers', 'pedagogical_supports')}
    warnings = []
    if not any(written.values()):
        warnings.append('El perfil pedagógico está vacío: la adaptación se basa solo en las NEE. Complétalo en la '
                        'ficha del estudiante para personalizarla.')
    elif not all(written.values()):
        missing = [name for key, name in (('learning_strengths', 'fortalezas'), ('learning_barriers', 'barreras'),
                                          ('pedagogical_supports', 'apoyos')) if not written[key]]
        warnings.append(f'Perfil incompleto: falta registrar {", ".join(missing)}.')
    if written['learning_barriers'] and not barrier_categories:
        warnings.append('No se pudo relacionar el texto de las barreras con categorías conocidas: revísalas tú.')
    if barrier_categories and not relevant_barriers:
        warnings.append('Ninguna barrera registrada parece relacionada con esta actividad; revisa si alguna aplica.')
    if unclassified:
        warnings.append(f'{unclassified} apoyo(s) registrado(s) no se pudieron clasificar: tenlos en cuenta al revisar.')
    if not (objective or skill):
        warnings.append('No hay objetivo ni destreza curricular registrados: se conserva el objetivo de la propia '
                        'actividad. Revísalo.')
    if any(written.values()):
        warnings.append(SHARED_NOTE if share_profile else PRIVACY_NOTE)

    result = {
        'demands': [{'category': k, 'label': DEMANDS[k], 'evidence': v} for k, v in demands.items()],
        'nee': nee, 'relevant_barriers': relevant_barriers, 'other_barriers': other_barriers,
        'usable_strengths': strengths, 'relevant_supports': supports, 'udl_suggestions': udl,
        'warnings': warnings, 'objective': objective, 'skill': skill,
        'profile_shared_with_ai': bool(share_profile and any(written.values())),
    }
    result['ai_prompt'] = ai_prompt(result, subject_name)
    return result


def minimized_profile(result):
    """What could be sent if an authorization ever covers it: fixed labels only, never the teacher's words."""
    return {'barriers': [b['label'] for b in result['relevant_barriers']],
            'supports': [s['label'] for s in result['relevant_supports']],
            'strengths': [s['label'] for s in result['usable_strengths']]}


def ai_prompt(result, subject_name):
    """The part of the engine the AI may receive: derived from the NEE, the subject and the activity only."""
    lines = ['Pertinencia de las NEE para esta actividad (calculada por el sistema con las NEE y la propia '
             'actividad, sin datos personales). Aplica las reglas de una NEE solo donde la actividad lo exige:']
    for item in result['nee']:
        lines.append(f'- {item["label"]}: {"aplica" if item["applies"] else "no aplica"}. {item["reason"]}')
    if any(not item['applies'] for item in result['nee']):
        lines.append(f'No agregues contenidos de otra materia ni dificultades que la actividad no tiene: se trata de '
                     f'{subject_name} y sus objetivos se conservan.')
    if result['objective'] or result['skill']:
        lines.append('Conserva el objetivo curricular: ' + ' '.join(
            part for part in (result['skill'], result['objective']) if part))
    lines.append('No reduzcas la dificultad curricular sin motivo ni des por hecho necesidades que no se indican. En '
                 '"pedagogical_justification" explica cada ajuste importante: la barrera que atiende (de las NEE que '
                 'aplican), el ajuste, el motivo, el objetivo curricular relacionado y un indicador observable para '
                 'comprobar si ayudó. Si no hay información suficiente para justificar un ajuste, dilo en el motivo. '
                 'No menciones diagnósticos que no estén en las condiciones del estudiante.')
    if result['profile_shared_with_ai'] and (result['relevant_barriers'] or result['relevant_supports']):
        # Authorized (setting + individual consent): fixed category labels only, never the teacher's words
        minimized = minimized_profile(result)
        lines.append('Perfil pedagógico del estudiante (categorías, sin datos personales): barreras observadas que '
                     f'se relacionan con la actividad: {", ".join(minimized["barriers"]) or "ninguna"}; apoyos '
                     f'individuales pertinentes: {", ".join(minimized["supports"]) or "ninguno"}; fortalezas: '
                     f'{", ".join(minimized["strengths"]) or "sin registrar"}. Úsalos solo donde la actividad lo exige.')
    return '\n'.join(lines)


def storable(result):
    """What is saved with the adaptation for the teacher's review (no free text of the profile)."""
    return {key: value for key, value in result.items() if key != 'ai_prompt'}


# ---------- Automatic checks of the AI's answer (they ask for review, they never validate) ----------

DIAGNOSIS_WORDS = {
    Condition.DYSLEXIA: ['dislexia'], Condition.DYSCALCULIA: ['discalculia'],
    Condition.ADHD: ['tdah', 'deficit de atencion', 'hiperactividad'],
    Condition.ASD: ['autismo', 'autista', 'espectro autista', 'tea'],
    Condition.ID_MILD: ['discapacidad intelectual'], Condition.ID_MODERATE: ['discapacidad intelectual'],
}
OTHER_DIAGNOSES = ['dispraxia', 'disgrafia', 'disortografia', 'sindrome de down', 'paralisis cerebral',
                   'trastorno del lenguaje', 'altas capacidades']


def mentions(text, word):
    """Whole-word match, so 'tea' is not found inside 'teatro'."""
    return re.search(rf'(?<![a-z]){re.escape(word)}(?![a-z])', text) is not None


def _justification_problems(content, conditions):
    problems = []
    items = content.get('pedagogical_justification')
    if items is None:
        return problems
    if not isinstance(items, list) or not items:
        return ['no trae la justificación pedagógica de los ajustes']
    incomplete = [n for n, item in enumerate(items, 1)
                  if not all(str(item.get(key, '')).strip() for key in ('adjustment', 'reason', 'indicator'))]
    if incomplete:
        problems.append(f'la justificación {", ".join(map(str, incomplete))} no tiene ajuste, motivo o indicador observable')
    text = plain(' '.join(str(item.get(key, '')) for item in items for key in ('barrier', 'reason', 'adjustment')))
    registered = {word for value in conditions for word in DIAGNOSIS_WORDS[Condition(value)]}
    invented = sorted({word for words in DIAGNOSIS_WORDS.values() for word in words if mentions(text, word)} - registered)
    invented += [word for word in OTHER_DIAGNOSES if mentions(text, word)]
    if invented:
        problems.append(f'menciona diagnósticos que el estudiante no tiene registrados: {", ".join(invented)}')
    return problems


def _student_text(content):
    parts = [content.get(key, '') for key in ('title', 'intro', 'learning_goal', 'closing_message', 'adapted_objective')]
    for step in content.get('steps', []):
        parts += [step.get('instruction', ''), step.get('hint', '')]
    for moment in content.get('moments', []):
        parts += [moment.get('student_task', '')]
    return plain(' '.join(str(p) for p in parts))


def review_checks(content, *, kind, conditions, student_name='', original=None):
    """
    Automatic checks before the adaptation is saved. They never validate it pedagogically: they list what the
    teacher must look at. kind: 'worksheet' or 'lesson'; original: the worksheet analysis or the lesson analysis.
    """
    original = original or {}
    problems = []
    if kind == 'worksheet':
        if not str(content.get('learning_goal', '')).strip():
            problems.append('no tiene objetivo de aprendizaje')
        expected = original.get('exercise_count') or 0
        if expected >= 4 and len(content.get('steps', [])) < expected // 2 and \
                Condition.ID_MODERATE not in conditions:
            problems.append(f'tiene {len(content.get("steps", []))} pasos y la guía original {expected} ejercicios: '
                            'revisa que no se haya perdido contenido esencial')
    else:
        if not str(content.get('adapted_objective', '')).strip():
            problems.append('no tiene objetivo adaptado')
        if not str(content.get('evaluation', '')).strip():
            problems.append('no explica cómo evaluar con el indicador de la clase')
        planned = [m.get('name', '') for m in original.get('moments', [])]
        adapted = [m.get('name', '') for m in content.get('moments', [])]
        if planned and len(adapted) < len(planned):
            problems.append(f'tiene {len(adapted)} momentos y la clase planificada {len(planned)}: no se deben '
                            'eliminar momentos ni sesiones sin revisión')
        durations = {plain(m.get('name')): plain(m.get('duration')) for m in original.get('moments', []) if m.get('duration')}
        changed = [m.get('name') for m in content.get('moments', [])
                   if durations.get(plain(m.get('name'))) and m.get('duration')
                   and plain(m.get('duration')) != durations[plain(m.get('name'))]]
        if changed:
            problems.append(f'cambió la duración de: {", ".join(changed[:4])}. Revisa si el cambio está justificado')
    problems += _justification_problems(content, conditions)
    student_text = _student_text(content)
    named = [word for word in plain(student_name).split() if len(word) > 2 and re.search(rf'\b{word}\b', student_text)]
    if named:
        problems.append('aparece el nombre del estudiante en el texto: quítalo antes de usarla')
    mentioned = sorted({word for words in DIAGNOSIS_WORDS.values() for word in words if mentions(student_text, word)})
    if mentioned:
        problems.append(f'el texto que lee el estudiante menciona diagnósticos ({", ".join(mentioned)})')
    return {'problems': problems, 'teacher_review_required': True}
