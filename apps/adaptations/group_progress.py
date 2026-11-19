"""
The history of the whole group: how each of the teacher's students (at most 3) has done, side by side, class by
class: in general (every subject together, the default) or in one subject.

Same data and rules as each student's history (learning_progress): only final evaluations, a trend only from
MIN_FOR_TREND evaluations. The general view can put subjects together because every point is measured against
the adapted objective of its own class (the scale is the same: below, at or above what was expected for that
student in that class); to compare contents, the teacher filters one subject. Each line measures a student against HIS OR HER OWN adapted
objective, so the lines are not a ranking: there is no group average and no order from best to worst. Built and
shown on this server for the teacher only; nothing of it goes to the AI.
"""
import re
from collections import Counter, OrderedDict

from apps.core.choices import RecordStatus

from . import learning_progress as lp

# One color per student, apart from the green / blue / orange of the zones
COLORS = ['#7c3aed', '#db2777', '#0891b2']
GOAL_LEVELS = ['Mucho menos (−2)', 'Menos (−1)', 'Lo esperado (0)', 'Más (+1)', 'Mucho más (+2)']
SUPPORT_LEVELS = ['Apoyo constante', 'Apoyo frecuente', 'Apoyo puntual', 'Sin apoyo']
INDICATORS = {
    'goal_attainment': {'title': 'Logro del objetivo adaptado', 'levels': GOAL_LEVELS, 'bands': True},
    'autonomy_level': {'title': 'Apoyo que necesitó en clase', 'levels': SUPPORT_LEVELS, 'bands': False},
}
GENERAL = ''  # every subject together
GENERAL_NOTE = ('Vista general: reúne las clases de todas las asignaturas. Cada punto se mide contra el objetivo '
                'adaptado de su propia clase; para ver un contenido en particular, elige una asignatura.')
NOT_A_RANKING = ('Cada línea compara al estudiante con su propio objetivo adaptado, que es distinto para cada uno: '
                 'el gráfico muestra cómo avanza cada uno, no ordena ni compara a los estudiantes entre sí.')
WIDTH, HEIGHT, LEFT, RIGHT, TOP, BOTTOM = 900, 260, 150, 36, 26, 52


def column_of(entry, with_subject=False):
    """
    (sort key, label) of the class: its week ('Sem. 21') or, without one, its date. With several subjects together,
    the subject tells apart two classes of the same week ('Sem. 21 · Mat.').
    """
    week = re.search(r'semana\s*(\d+)', entry.topic or '', re.IGNORECASE)
    subject = (entry.subject_code, f' · {entry.subject_name[:3]}.') if with_subject else ('', '')
    if week:
        return (0, int(week.group(1)), '', subject[0]), f'Sem. {week.group(1)}{subject[1]}'
    return (1, 0, entry.date.isoformat(), subject[0]), f'{entry.date:%d/%m}{subject[1]}'


def chart(indicator, columns, rows):
    """SVG coordinates of one indicator: a line per student over the shared columns (classes)."""
    spec = INDICATORS[indicator]
    order = lp.INDICATORS[indicator]['order']
    levels = spec['levels']
    span, plot_bottom = HEIGHT - TOP - BOTTOM, HEIGHT - BOTTOM

    def y_of(index):
        return TOP + (len(levels) - 1 - index) * span / (len(levels) - 1)

    inset = 30  # the first and last classes stay clear of the scale's labels and of the edge
    step = (WIDTH - LEFT - RIGHT - 2 * inset) / max(len(columns) - 1, 1)
    xs = [LEFT + inset + n * step if len(columns) > 1 else LEFT + (WIDTH - LEFT - RIGHT) / 2
          for n in range(len(columns))]
    lines = []
    for number, row in enumerate(rows):
        offset = (number - (len(rows) - 1) / 2) * 7  # students on the same value do not hide each other
        points = []
        for n, key in enumerate(columns):
            entry = row['by_column'].get(key)
            value = entry.values[indicator] if entry else ''
            if value in order:
                index = order.index(value)
                points.append({'x': lp.svg(xs[n] + offset), 'y': lp.svg(y_of(index)), 'entry': entry,
                               'label': entry.labels[indicator]})
        lines.append({'student': row['student'], 'color': row['color'], 'points': points,
                      'polyline': ' '.join(f'{p["x"]},{p["y"]}' for p in points)})
    bands = []
    if spec['bands']:  # goal: above (+1, +2), expected (0) and below (−1, −2)
        bands = [{'tone': 'above', 'y': lp.svg(TOP), 'h': lp.svg(y_of(2.5) - TOP)},
                 {'tone': 'expected', 'y': lp.svg(y_of(2.5)), 'h': lp.svg(y_of(1.5) - y_of(2.5))},
                 {'tone': 'below', 'y': lp.svg(y_of(1.5)), 'h': lp.svg(plot_bottom - y_of(1.5))}]
    return {'indicator': indicator, 'title': spec['title'], 'width': WIDTH, 'height': HEIGHT, 'left': LEFT,
            'right': WIDTH - RIGHT, 'plot_width': lp.svg(WIDTH - LEFT - RIGHT), 'bottom': plot_bottom,
            'label_x': LEFT - 12, 'name_y': plot_bottom + 22,
            'levels': [{'label': label, 'y': lp.svg(y_of(i)), 'zero': spec['bands'] and i == 2}
                       for i, label in enumerate(levels)],
            'columns': [{'x': lp.svg(x), 'label': columns[key]} for x, key in zip(xs, columns)],
            'bands': bands, 'lines': lines, 'has_points': any(line['points'] for line in lines)}


def summary(entries, indicator):
    item = lp.describe_series(entries, indicator)
    return {'trend': item['trend'], 'label': lp.TREND_LABELS.get(item['trend'], 'Sin tendencia aún'),
            'text': item['text']}


def group_progress(teacher, subject_code=GENERAL):
    """Everything for the group's history (general, or one subject); works with no students or evaluations."""
    students = list(teacher.students.filter(record_status=RecordStatus.ACTIVE).order_by('created_at'))
    histories = [(student, lp.history(student)) for student in students]
    counts = Counter(e.subject_code for _student, entries in histories for e in entries)
    subjects = list(OrderedDict((e.subject_code, e.subject_name) for _s, entries in histories for e in entries).items())
    if subject_code not in counts:
        subject_code = GENERAL
    with_subject = subject_code == GENERAL and len(counts) > 1
    columns = {}
    rows = []
    for number, (student, entries) in enumerate(histories):
        # In the order of the classes (their week), which is how the lines are drawn, not the order of registration
        chosen = sorted((e for e in entries if subject_code in (GENERAL, e.subject_code)),
                        key=lambda e: column_of(e, with_subject)[0])
        by_column = {}
        for entry in chosen:  # a later evaluation of the same class takes its place
            key, label = column_of(entry, with_subject)
            columns[key] = label
            by_column[key] = entry
        supports = lp.supports_summary(chosen)['applied']
        useful = max(supports, key=lambda item: (item['useful'], item['count']), default=None)
        last = chosen[-1] if chosen else None
        rows.append({
            'student': student, 'color': COLORS[number % len(COLORS)], 'count': len(chosen), 'by_column': by_column,
            'goal': summary(chosen, 'goal_attainment'), 'support': summary(chosen, 'autonomy_level'),
            'last_goal': last.labels['goal_attainment'] if last else '',
            'last_support': last.labels['autonomy_level'] if last else '',
            'useful_support': useful['label'] if useful and useful['useful'] else '',
        })
    columns = OrderedDict(sorted(columns.items()))
    return {
        'students': rows, 'subjects': subjects, 'subject_code': subject_code, 'general': subject_code == GENERAL,
        'subject_name': dict(subjects).get(subject_code, 'Todas las asignaturas'),
        'general_note': GENERAL_NOTE if subject_code == GENERAL and len(counts) > 1 else '',
        'total': sum(row['count'] for row in rows),
        'evaluated': sum(1 for row in rows if row['count']), 'class_count': len(columns),
        'charts': [chart(name, columns, rows) for name in INDICATORS] if columns else [],
        'not_a_ranking': NOT_A_RANKING, 'observed_note': lp.OBSERVED_NOTE, 'local_only': lp.LOCAL_ONLY,
        'min_for_trend': lp.MIN_FOR_TREND,
    }
