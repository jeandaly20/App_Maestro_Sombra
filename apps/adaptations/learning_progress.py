"""
Task 4: the learning follow-up of each student, built only from the teacher's final classroom evaluations.

Everything here is descriptive and runs in this server. It never sends anything to the AI: the history, its
summaries and its recommendations are shown to the teacher only (the AI keeps receiving what Task 3.1
authorizes). It does not diagnose, predict or rank students, and it does not say that a support caused a result.

Design decisions (documented thresholds, not statistical tests):
- MIN_FOR_TREND = 3. With one evaluation there is no evolution; two only show a difference between two
  moments. From three comparable evaluations the system describes the direction of the registered values,
  always as "evolución observada", never as a demonstrated improvement.
- REPEATED = 2. "Reiterado" means it was registered in at least two evaluations, the smallest number that
  is not a single event. It triggers a recommendation to review, never a conclusion.
- Activities are compared only inside the same subject and the same curricular skill (destreza) or, when the
  activity has none, the same topic. Different subjects are never mixed into a general result.
- Every indicator uses its own fixed scale (models.py of evaluations); a value that was not registered is
  left out, never filled in.
"""
import hashlib
import re
import unicodedata
from collections import Counter, OrderedDict
from dataclasses import dataclass, field

from apps.core.choices import RecordStatus
from apps.evaluations.models import (
    SUPPORT_CHOICES, AchievementLevel, AutonomyLevel, Comprehension, Evaluation, EvaluationStatus, GoalAttainment,
    Overload, RecommendationReview,
)

from .models import LessonAdaptation

MIN_FOR_TREND = 3
REPEATED = 2
INSUFFICIENT = 'Información insuficiente para establecer una tendencia.'
LOCAL_ONLY = ('El historial, sus resúmenes y sus recomendaciones se calculan y se muestran solo en el servidor: '
              'no se envían a la IA.')
OBSERVED_NOTE = ('Evolución observada: describe los valores registrados por el docente. No demuestra una mejora ni '
                 'permite atribuir el cambio a un apoyo: otros factores (el tema, el día, la dificultad de la '
                 'actividad) también influyen.')

# Each indicator, its scale in order (lowest first) and how to read the order. The order only places the
# registered values on a line for the chart and the description; it is not a score of the student.
INDICATORS = OrderedDict([
    ('goal_attainment', {'label': 'Logro del objetivo', 'order': list(GoalAttainment.values),
                         'choices': GoalAttainment, 'rises': 'más cercanos o por encima de lo esperado',
                         'falls': 'más por debajo de lo esperado'}),
    ('comprehension', {'label': 'Comprensión', 'order': [Comprehension.NO, Comprehension.DIFFICULTY, Comprehension.YES],
                       'choices': Comprehension, 'rises': 'de mayor comprensión',
                       'falls': 'de menor comprensión'}),
    ('autonomy_level', {'label': 'Nivel de apoyo', 'order': [AutonomyLevel.CONSTANT_SUPPORT, AutonomyLevel.FREQUENT_SUPPORT,
                                                             AutonomyLevel.OCCASIONAL_SUPPORT, AutonomyLevel.AUTONOMOUS],
                        'choices': AutonomyLevel, 'rises': 'de menor apoyo directo',
                        'falls': 'de mayor apoyo directo'}),
    ('overload', {'label': 'Sobrecarga cognitiva', 'order': [Overload.EVIDENT, Overload.MILD, Overload.NONE],
                  'choices': Overload, 'rises': 'de menor sobrecarga', 'falls': 'de mayor sobrecarga'}),
    ('achievement_level', {'label': 'Escala del Ministerio', 'order': [AchievementLevel.NOT_YET, AchievementLevel.NEAR,
                                                                      AchievementLevel.ACHIEVES, AchievementLevel.MASTERS],
                           'choices': AchievementLevel, 'rises': 'más altos en la escala',
                           'falls': 'más bajos en la escala'}),
])
GOAL_SCORE = {'MUCH_LESS': -2, 'LESS': -1, 'EXPECTED': 0, 'MORE': 1, 'MUCH_MORE': 2}
DIFFICULTY = {'comprehension': {Comprehension.DIFFICULTY, Comprehension.NO},
              'goal_attainment': {GoalAttainment.MUCH_LESS, GoalAttainment.LESS}}
OVERLOAD_SIGNS = {Overload.MILD, Overload.EVIDENT}
SUPPORT_LABELS = dict(SUPPORT_CHOICES)
SKILL_CODE = re.compile(r'\b([A-Z]{1,4}\.\d+\.\d+\.\d+(?:\.\d+)?)\b')


def normalized(text):
    text = unicodedata.normalize('NFKD', str(text or '')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def plural(count, one, many):
    return f'{count} {one if count == 1 else many}'


def short_hash(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode()).hexdigest()[:16]


# ---------- One evaluated activity ----------

@dataclass
class Entry:
    """One final evaluation, with what can be shown in the history. The observation is never copied here."""
    evaluation_id: str
    adaptation_id: str
    code: str
    date: object
    subject_code: str
    subject_name: str
    topic: str
    activity: str
    objective: str
    objective_source: str
    skill: str
    version: int
    source_code: str
    worksheet_active: bool
    from_lesson: bool
    has_observation: bool
    regeneration_requested: bool
    values: dict
    labels: dict
    supports_suggested: list = field(default_factory=list)
    supports_in_material: list = field(default_factory=list)
    supports_applied: list = field(default_factory=list)
    supports_useful: list = field(default_factory=list)
    has_supports_note: bool = False
    regenerated_codes: list = field(default_factory=list)

    @property
    def is_regeneration(self):
        return self.version > 1

    @property
    def goal_score(self):
        return GOAL_SCORE.get(self.values.get('goal_attainment'))

    @property
    def comparison_key(self):
        """Activities are compared only within one subject and one skill (or, without a skill, one topic)."""
        if self.skill:
            code = SKILL_CODE.search(self.skill)
            return (self.subject_code, 'skill', code.group(1) if code else normalized(self.skill))
        return (self.subject_code, 'topic', normalized(self.topic))

    @property
    def comparison_label(self):
        return f'Destreza: {self.skill.rstrip(". ")}' if self.skill else f'Tema: {self.topic}'

    @property
    def comparison_phrase(self):
        """'de la destreza «…»' or 'del tema «…»', for the recommendations."""
        return f'de la destreza «{self.skill.rstrip(". ")}»' if self.skill else f'del tema «{self.topic}»'


def valid_evaluations(student):
    """Only final, active evaluations of this student's worksheets (drafts and discarded records are left out)."""
    return (Evaluation.objects
            .filter(adaptation__student=student, status=EvaluationStatus.FINAL, record_status=RecordStatus.ACTIVE)
            .select_related('adaptation__material__subject', 'adaptation__source_adaptation')
            .prefetch_related('adaptation__regenerations')
            .order_by('created_at', 'id'))


def _lesson_contexts(student):
    """{material id: the adapted class built on that material}: where the curricular objective and skill live."""
    lessons = (LessonAdaptation.objects.filter(student=student, lesson__material__isnull=False)
               .select_related('lesson').order_by('created_at'))
    return {item.lesson.material_id: item for item in lessons}


def _suggested(content):
    """Supports the relevance engine suggested for this worksheet (stored with it): suggestions, not applied."""
    relevance = (content or {}).get('relevance') or {}
    labels = [item['label'] for item in relevance.get('relevant_supports', []) if item.get('label')]
    labels += [item['label'] for item in relevance.get('udl_suggestions', []) if item.get('pertinent') and item.get('label')]
    return list(dict.fromkeys(labels))


def entry_of(evaluation, lessons):
    adaptation = evaluation.adaptation
    material = adaptation.material
    lesson = lessons.get(material.id)
    lesson_relevance = ((lesson.content or {}).get('relevance') or {}) if lesson else {}
    skill = (lesson_relevance.get('skill') or '').strip()
    objective, source = (lesson_relevance.get('objective') or '').strip(), 'Objetivo de la clase'
    if not objective and lesson:
        objective, source = (lesson.content or {}).get('adapted_objective', '').strip(), 'Objetivo adaptado de la clase'
    if not objective:
        objective, source = (adaptation.content or {}).get('learning_goal', '').strip(), 'Meta de la ficha'
    values = {name: getattr(evaluation, name) or '' for name in INDICATORS}
    labels = {name: (getattr(evaluation, f'get_{name}_display')() if values[name] else '') for name in INDICATORS}
    return Entry(
        evaluation_id=str(evaluation.pk), adaptation_id=str(adaptation.pk), code=adaptation.code,
        date=evaluation.created_at, subject_code=material.subject.code, subject_name=material.subject.name,
        topic=material.topic, activity=adaptation.title, objective=objective, objective_source=source if objective else '',
        skill=skill, version=adaptation.version,
        source_code=adaptation.source_adaptation.code if adaptation.source_adaptation else '',
        worksheet_active=adaptation.record_status == RecordStatus.ACTIVE, from_lesson=bool(lesson),
        has_observation=bool(evaluation.observation), regeneration_requested=evaluation.regeneration_requested,
        values=values, labels=labels, supports_suggested=_suggested(adaptation.content),
        supports_in_material=adaptation.adjustment_labels,
        supports_applied=[SUPPORT_LABELS[k] for k in evaluation.supports_applied if k in SUPPORT_LABELS],
        supports_useful=[SUPPORT_LABELS[k] for k in evaluation.supports_useful if k in SUPPORT_LABELS],
        has_supports_note=bool(evaluation.supports_note),
        regenerated_codes=[version.code for version in adaptation.regenerations.all()],
    )


def history(student, subject_code=''):
    """The student's evaluated activities, oldest first, optionally of one subject."""
    lessons = _lesson_contexts(student)
    entries = [entry_of(evaluation, lessons) for evaluation in valid_evaluations(student)]
    return [e for e in entries if not subject_code or e.subject_code == subject_code]


# ---------- Descriptive analysis ----------

def distribution(entries, name):
    """How many evaluations registered each level of one indicator, plus those that did not register it."""
    choices = INDICATORS[name]['choices']
    counts = Counter(e.values[name] for e in entries)
    rows = [{'value': value, 'label': choices(value).label, 'count': counts.get(value, 0)}
            for value in reversed(INDICATORS[name]['order'])]
    return {'rows': rows, 'missing': counts.get('', 0), 'total': len(entries)}


def describe_series(entries, name):
    """
    The observed evolution of one indicator over comparable activities, using only the evaluations that
    registered it. Below MIN_FOR_TREND it says so instead of describing a direction.
    """
    order = INDICATORS[name]['order']
    points = [(e, order.index(e.values[name])) for e in entries if e.values[name] in order]
    result = {'indicator': name, 'label': INDICATORS[name]['label'], 'count': len(points),
              'missing': len(entries) - len(points), 'trend': None, 'text': INSUFFICIENT}
    if len(points) < MIN_FOR_TREND:
        return result
    steps = [b - a for (_, a), (_, b) in zip(points, points[1:])]
    first, last = points[0][0].labels[name], points[-1][0].labels[name]
    if all(step == 0 for step in steps):
        trend, text = 'stable', f'Se registró el mismo valor en las {len(points)} evaluaciones ({last}).'
    elif all(step >= 0 for step in steps):
        trend, text = 'up', (f'En las actividades más recientes se registraron valores {INDICATORS[name]["rises"]} '
                             f'(de «{first}» a «{last}», {len(points)} evaluaciones).')
    elif all(step <= 0 for step in steps):
        trend, text = 'down', (f'En las actividades más recientes se registraron valores {INDICATORS[name]["falls"]} '
                               f'(de «{first}» a «{last}», {len(points)} evaluaciones): conviene revisarlo.')
    else:
        trend, text = 'variable', (f'Los valores registrados variaron sin una dirección constante '
                                   f'({len(points)} evaluaciones, de «{first}» a «{last}»).')
    result.update(trend=trend, text=text)
    return result


def comparable_groups(entries):
    """
    The activities that can be compared: same subject and same skill (or topic). A group with a single
    activity is shown on its own, without any trend.
    """
    groups = OrderedDict()
    for entry in entries:
        groups.setdefault(entry.comparison_key, []).append(entry)
    result = []
    for key, items in groups.items():
        series = [describe_series(items, name) for name in INDICATORS]
        result.append({
            'key': short_hash(*key), 'subject_code': key[0], 'subject_name': items[0].subject_name,
            'label': items[0].comparison_label, 'by_skill': key[1] == 'skill', 'entries': items,
            'count': len(items), 'comparable': len(items) >= MIN_FOR_TREND,
            'series': series if len(items) >= MIN_FOR_TREND else [],
            'message': '' if len(items) >= MIN_FOR_TREND else (
                INSUFFICIENT if len(items) > 1 else 'Una sola actividad evaluada: se muestra su resultado, sin tendencia.'),
        })
    return result


def supports_summary(entries):
    """Suggested, built into the worksheet, applied in class and judged useful: four different things."""
    def counted(attribute):
        counter = Counter(label for e in entries for label in getattr(e, attribute))
        return [{'label': label, 'count': count} for label, count in counter.most_common()]
    applied = counted('supports_applied')
    useful = dict((item['label'], item['count']) for item in counted('supports_useful'))
    for item in applied:
        item['useful'] = useful.get(item['label'], 0)
    return {
        'suggested': counted('supports_suggested'), 'in_material': counted('supports_in_material'),
        'applied': applied, 'not_recorded': sum(1 for e in entries if not e.supports_applied),
        'total': len(entries),
    }


def overload_summary(entries):
    registered = [e for e in entries if e.values['overload']]
    signs = [e for e in registered if e.values['overload'] in OVERLOAD_SIGNS]
    return {'registered': len(registered), 'with_signs': len(signs),
            'evident': sum(1 for e in signs if e.values['overload'] == Overload.EVIDENT),
            'missing': len(entries) - len(registered)}


def insufficient_areas(entries, subjects):
    """What the data cannot tell yet: subjects with few evaluations and indicators left unregistered."""
    areas = []
    for code, name in subjects:
        items = [e for e in entries if e.subject_code == code]
        if len(items) < MIN_FOR_TREND:
            areas.append(f'{name}: {len(items)} actividad{"es" if len(items) != 1 else ""} evaluada'
                         f'{"s" if len(items) != 1 else ""} (se necesitan al menos {MIN_FOR_TREND} comparables para '
                         f'describir una evolución).')
    for name, spec in INDICATORS.items():
        missing = sum(1 for e in entries if not e.values[name])
        if entries and missing:
            areas.append(f'{spec["label"]}: sin registrar en {missing} de {len(entries)} evaluaciones.')
    not_recorded = sum(1 for e in entries if not e.supports_applied)
    if entries and not_recorded:
        areas.append(f'Apoyos aplicados durante la actividad: sin registrar en {not_recorded} de {len(entries)} '
                     'evaluaciones (no se suponen a partir de los sugeridos).')
    return areas


def svg(number):
    """A coordinate for the SVG with a decimal point (the Spanish locale would print a comma)."""
    return f'{number:.1f}'


def chart(entries):
    """
    Points for the achievement chart of ONE subject: only evaluations that registered the goal attainment,
    at their own date order. Nothing is interpolated; the others are listed apart.
    """
    plotted = [e for e in entries if e.goal_score is not None]
    width, height, left, right, top, bottom = 680, 220, 200, 24, 16, 34
    step = (width - left - right) / max(len(plotted) - 1, 1)
    points = []
    for n, entry in enumerate(plotted):
        x = left + (n * step if len(plotted) > 1 else (width - left - right) / 2)
        y = top + (2 - entry.goal_score) * (height - top - bottom) / 4
        points.append({'x': svg(x), 'y': svg(y), 'entry': entry, 'label': entry.labels['goal_attainment']})
    levels = [{'label': GoalAttainment(value).label, 'y': svg(top + (2 - GOAL_SCORE[value]) * (height - top - bottom) / 4)}
              for value in GoalAttainment.values]
    return {'width': width, 'height': height, 'left': left, 'label_x': left - 8, 'right': width - right,
            'bottom': height - bottom, 'date_y': height - bottom + 20,
            'points': points, 'levels': levels, 'polyline': ' '.join(f'{p["x"]},{p["y"]}' for p in points),
            'not_plotted': [e for e in entries if e.goal_score is None]}


# ---------- Deterministic recommendations ----------

def _recommendation(key, text, reason, evidence, entries, limitations, subject=''):
    return {'key': key, 'text': text, 'reason': reason, 'evidence': evidence,
            'activities': [{'code': e.code, 'date': e.date, 'topic': e.topic, 'adaptation_id': e.adaptation_id}
                           for e in entries],
            'limitations': limitations, 'subject_code': subject,
            'fingerprint': short_hash(key, *sorted(e.evaluation_id for e in entries))}


def recommendations(entries, subjects):
    """
    Recommendations for the teacher to review, derived from the registered values by fixed rules. Each one
    carries its evidence, the related activities, its reason and its limits. None of them changes the
    profile, suggests a diagnosis or credits a support with a result.
    """
    result = []
    for code, name in subjects:
        items = [e for e in entries if e.subject_code == code]
        if len(items) < MIN_FOR_TREND:
            result.append(_recommendation(
                f'insuficiente:{code}', f'No existen evaluaciones suficientes en {name} para identificar una tendencia.',
                f'{plural(len(items), "actividad evaluada", "actividades evaluadas")} en {name}; se necesitan al menos '
                f'{MIN_FOR_TREND} comparables.',
                f'{plural(len(items), "evaluación finalizada", "evaluaciones finalizadas")}.', items,
                'Mientras tanto, cada resultado se interpreta por separado.', code))
    for group in comparable_groups(entries):
        items = group['entries']
        hard = [e for e in items if e.values['comprehension'] in DIFFICULTY['comprehension']
                or e.values['goal_attainment'] in DIFFICULTY['goal_attainment']]
        if len(hard) >= REPEATED:
            result.append(_recommendation(
                f'dificultad:{group["key"]}',
                f'Conviene revisar las actividades {items[0].comparison_phrase} ({group["subject_name"]}), donde se '
                'registraron dificultades reiteradas.',
                f'En {len(hard)} de {len(items)} actividades se registró dificultad de comprensión o un logro por debajo '
                'de lo esperado.',
                ', '.join(f'{e.code}: {e.labels["comprehension"] or "comprensión sin registrar"}'
                          f'{"; " + e.labels["goal_attainment"] if e.labels["goal_attainment"] else ""}' for e in hard),
                hard, 'Se basa solo en lo registrado tras cada actividad; no explica la causa de la dificultad.',
                group['subject_code']))
        overloaded = [e for e in items if e.values['overload'] in OVERLOAD_SIGNS]
        if len(overloaded) >= REPEATED:
            result.append(_recommendation(
                f'sobrecarga:{group["key"]}',
                f'Se registraron signos de sobrecarga en varias actividades {items[0].comparison_phrase}; conviene '
                'revisar la extensión y el ritmo de esas actividades.',
                f'Sobrecarga leve o evidente en {len(overloaded)} de {len(items)} actividades.',
                ', '.join(f'{e.code}: {e.labels["overload"]}' for e in overloaded), overloaded,
                'La sobrecarga registrada puede deberse a muchos factores del día y del aula.', group['subject_code']))
    applied = Counter(label for e in entries for label in e.supports_applied)
    for label, count in applied.most_common():
        if count < REPEATED:
            continue
        related = [e for e in entries if label in e.supports_applied]
        useful = sum(1 for e in related if label in e.supports_useful)
        result.append(_recommendation(
            f'apoyo:{short_hash(label)}',
            f'Se ha utilizado el apoyo «{label}» en varias actividades; el docente puede valorar si conviene mantenerlo.',
            f'Registrado como aplicado en {count} actividades'
            + (f' y marcado como útil en {useful}.' if useful else '; no se marcó como útil en ninguna.'),
            ', '.join(e.code for e in related), related,
            'Que un apoyo se haya aplicado no demuestra que haya causado los resultados de esas actividades.'))
    # A newer version exists (the regeneration was made) but no evaluation of it has been finished
    evaluated = {e.code for e in entries}
    unevaluated = [e for e in entries if e.regeneration_requested and e.regenerated_codes
                   and not set(e.regenerated_codes) & evaluated]
    if unevaluated:
        result.append(_recommendation(
            'regeneracion:' + short_hash(*sorted(e.code for e in unevaluated)),
            'Hay versiones regeneradas que todavía no se evalúan en el aula.',
            'Se pidió una versión más simplificada después de estas evaluaciones y no hay una evaluación de la versión nueva.',
            ', '.join(e.code for e in unevaluated), unevaluated,
            'Sin esa evaluación no se puede saber cómo funcionó la versión nueva.'))
    return result


def with_reviews(student, items):
    """Marks each recommendation as pending, or with the teacher's decision while its evidence is the same."""
    reviews = {review.key: review for review in RecommendationReview.objects.filter(student=student)}
    for item in items:
        review = reviews.get(item['key'])
        current = review if review and review.evidence == item['fingerprint'] else None
        item['review'] = current
        item['pending'] = current is None
    return items


# ---------- Everything for the panel ----------

def progress(student, subject_code=''):
    """The data of the "Historial de aprendizaje" panel. Works with no evaluations at all."""
    everything = history(student)
    subjects = list(OrderedDict((e.subject_code, e.subject_name) for e in everything).items())
    chosen = [s for s in subjects if s[0] == subject_code] if subject_code else subjects
    entries = [e for e in everything if not subject_code or e.subject_code == subject_code]
    drafts = Evaluation.objects.filter(adaptation__student=student, status=EvaluationStatus.DRAFT,
                                       record_status=RecordStatus.ACTIVE).count()
    items = with_reviews(student, recommendations(entries, chosen))
    return {
        'entries': entries, 'subjects': subjects, 'subject_code': subject_code, 'total': len(entries),
        'drafts': drafts, 'by_subject': [{'code': code, 'name': name,
                                          'count': sum(1 for e in entries if e.subject_code == code),
                                          'goal': distribution([e for e in entries if e.subject_code == code], 'goal_attainment'),
                                          'chart': chart([e for e in entries if e.subject_code == code])}
                                         for code, name in chosen],
        'groups': comparable_groups(entries), 'supports': supports_summary(entries),
        'overload': overload_summary(entries), 'autonomy': distribution(entries, 'autonomy_level'),
        'comprehension': distribution(entries, 'comprehension'),
        'insufficient': insufficient_areas(entries, chosen), 'recommendations': items,
        'pending': sum(1 for item in items if item['pending']),
        'min_for_trend': MIN_FOR_TREND, 'insufficient_text': INSUFFICIENT, 'observed_note': OBSERVED_NOTE,
        'local_only': LOCAL_ONLY,
    }


# ---------- Before a new adaptation (local only) ----------

def adaptation_context(student, subject_code, *, skill='', topic=''):
    """
    A short local summary of the student's related history, shown to the teacher next to a new adaptation.
    It is stored with the adaptation for the review screen and is NEVER part of what the AI receives.
    Related means the same subject; the activities of the same skill (or topic) are marked as comparable.
    """
    entries = history(student, subject_code)
    if not entries:
        return {'count': 0, 'related': [], 'recommendations': [], 'note': LOCAL_ONLY,
                'message': 'Sin evaluaciones anteriores en esta materia.'}
    code = SKILL_CODE.search(skill or '')
    target = (subject_code, 'skill', code.group(1) if code else normalized(skill)) if skill else \
        (subject_code, 'topic', normalized(topic))
    related = [{'code': e.code, 'date': e.date.isoformat(), 'topic': e.topic, 'comparable': e.comparison_key == target,
                'goal': e.labels['goal_attainment'], 'comprehension': e.labels['comprehension'],
                'support': e.labels['autonomy_level'], 'overload': e.labels['overload'],
                'applied': e.supports_applied} for e in entries[-5:]]
    subjects = [(subject_code, entries[0].subject_name)]
    items = with_reviews(student, recommendations(entries, subjects))
    return {'count': len(entries), 'related': related, 'note': LOCAL_ONLY, 'message': '',
            'comparable_count': sum(1 for e in entries if e.comparison_key == target),
            'recommendations': [{'text': item['text'], 'reason': item['reason'], 'limitations': item['limitations'],
                                 'pending': item['pending']} for item in items]}
