"""
The progress report of one student: everything the teacher registered after each class, in one document for the
institution (DECE, coordination), instead of downloading and summarizing the report of every class by hand.

It is built from the same data and rules as the "Historial de aprendizaje" (adaptations.learning_progress): only
final evaluations, compared within each subject, a trend only from MIN_FOR_TREND evaluations. Unlike the history
page it includes the teacher's observations, because it is the paperwork the teacher hands in. It is built on this
server for the student's own teacher only; nothing of it is sent to the AI. It describes; it does not diagnose.
"""
import re
from collections import OrderedDict

from django.utils import timezone

from apps.adaptations import learning_progress as lp
from apps.core.apa import ApaDocument

from .models import Evaluation

TITLE = 'Informe de Avance del Estudiante'
CONFIDENTIAL = ('Este documento es confidencial: contiene datos personales de un estudiante con necesidades educativas '
                'específicas, protegidos por la Ley Orgánica de Protección de Datos Personales (LOPDP). Debe '
                'entregarse solo a quien corresponda dentro de la institución.')
NOT_A_DIAGNOSIS = ('Este informe describe lo observado y registrado por el docente después de cada clase. No es un '
                   'diagnóstico ni atribuye los resultados a un apoyo concreto.')
# Short labels for the tables; the table notes explain them
GOAL_SHORT = {'MUCH_LESS': '−2', 'LESS': '−1', 'EXPECTED': '0', 'MORE': '+1', 'MUCH_MORE': '+2'}
LEVEL_SHORT = {'MASTERS': 'Domina', 'ACHIEVES': 'Alcanza', 'NEAR': 'Próximo a alcanzar', 'NOT_YET': 'No alcanza'}
SUPPORT_SHORT = {'AUTONOMOUS': 'Ninguno', 'OCCASIONAL_SUPPORT': 'Puntual', 'FREQUENT_SUPPORT': 'Frecuente',
                 'CONSTANT_SUPPORT': 'Constante'}
SHORT = {'goal_attainment': GOAL_SHORT, 'achievement_level': LEVEL_SHORT, 'autonomy_level': SUPPORT_SHORT}
TREND = {'up': 'Mejora observada', 'down': 'Retroceso observado', 'stable': 'Se mantiene',
         'variable': 'Variable, sin dirección constante'}
GOAL_LEVELS = ['Mucho menos (−2)', 'Menos (−1)', 'Lo esperado (0)', 'Más (+1)', 'Mucho más (+2)']
SCALE_NOTE = ('Logro: −2 mucho menos, −1 menos, 0 lo esperado, +1 más y +2 mucho más de lo esperado. Escala del '
              'Reglamento General a la LOEI (art. 26): domina (9,00 a 10,00), alcanza (7,00 a 8,99), próximo a '
              'alcanzar (4,01 a 6,99) y no alcanza (4,00 o menos). Apoyo: el que necesitó el estudiante durante la clase.')
DATE = '%d/%m/%Y'


def short(entry, name):
    return SHORT[name].get(entry.values[name], 'Sin registrar')


def point_name(entry):
    """The label under a point of the chart: the week of the class ('Sem. 21') or, without it, the date."""
    week = re.search(r'semana\s*(\d+)', entry.topic or '', re.IGNORECASE)
    return f'Sem. {week.group(1)}' if week else timezone.localtime(entry.date).strftime('%d/%m')


def class_name(entry):
    return entry.topic.replace(' · ', ': ') or entry.activity


def trend_cell(series, name):
    """'Mejora observada: de −1 a +1' for the summary table, or why there is no trend."""
    item = next(s for s in series if s['indicator'] == name)
    if not item['trend']:
        return 'Información insuficiente'
    ends = item['ends']
    return TREND[item['trend']] + (f': de {ends[0]} a {ends[1]}' if ends else '')


def describe(entries, name):
    """The series of one indicator, with the first and last short labels for the summary table."""
    item = lp.describe_series(entries, name)
    registered = [e for e in entries if e.values[name]]
    item['ends'] = [short(registered[0], name), short(registered[-1], name)] if registered else []
    return item


def in_period(entries, start, end):
    return [e for e in entries
            if (not start or timezone.localtime(e.date).date() >= start)
            and (not end or timezone.localtime(e.date).date() <= end)]


def period_label(entries, start, end):
    first = start or (timezone.localtime(entries[0].date).date() if entries else None)
    last = end or timezone.localdate()
    return f'Del {first:{DATE}} al {last:{DATE}}' if first else f'Hasta el {last:{DATE}}'


def progress_report_pdf(student, start=None, end=None):
    """The report as PDF bytes, or None when the period has no final evaluation."""
    entries = in_period(lp.history(student), start, end)
    if not entries:
        return None
    subjects = list(OrderedDict((e.subject_code, e.subject_name) for e in entries).items())
    groups = lp.comparable_groups(entries)
    evaluations = {str(e.pk): e for e in Evaluation.objects.filter(pk__in=[x.evaluation_id for x in entries])}
    period = period_label(entries, start, end)

    doc = ApaDocument(TITLE, period)
    doc.text(CONFIDENTIAL)

    # ----- General data -----
    doc.heading('Datos Generales')
    doc.fields('Datos del estudiante y del periodo evaluado', [
        ('Estudiante', student.name),
        ('Grado', student.get_grade_display()),
        ('Necesidades educativas específicas', ', '.join(student.condition_labels)),
        ('Docente', student.teacher.full_name),
        ('Periodo del informe', period),
        ('Evaluaciones finalizadas', f'{lp.plural(len(entries), "clase evaluada", "clases evaluadas")} en '
                                     f'{lp.plural(len(subjects), "asignatura", "asignaturas")}'),
    ])

    # ----- Summary per subject -----
    doc.heading('Resumen por Asignatura')
    rows = []
    for group in groups:
        items = group['entries']
        series = [describe(items, name) for name in ('goal_attainment', 'autonomy_level', 'achievement_level')]
        if group['comparable']:
            goal = series[0]['text']
            doc.text(f'En {group["subject_name"]} se evaluaron {len(items)} clases. Logro del objetivo: '
                     f'{goal[0].lower() + goal[1:]}')
        else:
            doc.text(f'En {group["subject_name"]} se evaluaron {lp.plural(len(items), "clase", "clases")}, por lo '
                     'que la información es insuficiente para establecer una tendencia.')
        rows.append([group['subject_name'], str(len(items))] + [trend_cell(series, s['indicator']) for s in series])
    doc.table('Evolución observada por asignatura',
              ['Asignatura', 'Clases', 'Logro del objetivo', 'Apoyo que necesitó', 'Escala del Ministerio'], rows,
              (0.22, 0.10, 0.23, 0.22, 0.23),
              f'La evolución se describe solo con {lp.MIN_FOR_TREND} evaluaciones o más de la misma asignatura. '
              'Describe los valores registrados por el docente; no demuestra una mejora ni la atribuye a un apoyo.')

    # ----- Class by class -----
    doc.heading('Evolución Clase por Clase')
    for group in groups:
        items = group['entries']
        if len(groups) > 1:
            doc.heading(group['subject_name'], level=2)
        plotted = [e for e in items if e.goal_score is not None]
        if len(plotted) >= 2:
            doc.line_chart(f'Logro del objetivo adaptado en las clases de {group["subject_name"]}', GOAL_LEVELS,
                           [e.goal_score + 2 for e in plotted],
                           [point_name(e) for e in plotted],
                           'Escala de logro del objetivo adaptado registrada por el docente, de −2 (mucho menos '
                           'de lo esperado) a +2 (mucho más de lo esperado), en el orden de las clases.')
        doc.table(f'Resultados de cada clase de {group["subject_name"]}',
                  ['Fecha', 'Clase', 'Logro', 'Escala del Ministerio', 'Apoyo'],
                  [[timezone.localtime(e.date).strftime(DATE), class_name(e), short(e, 'goal_attainment'),
                    short(e, 'achievement_level'), short(e, 'autonomy_level')] for e in items],
                  (0.15, 0.37, 0.10, 0.22, 0.16), SCALE_NOTE)

    # ----- Supports -----
    doc.heading('Apoyos Utilizados')
    supports = lp.supports_summary(entries)
    if supports['applied']:
        doc.table('Apoyos aplicados durante las clases y su utilidad', ['Apoyo', 'Se aplicó', 'Resultó útil'],
                  [[item['label'], f'{item["count"]} de {lp.plural(len(entries), "clase", "clases")}',
                    str(item['useful'])] for item in supports['applied']],
                  (0.50, 0.28, 0.22),
                  'Se cuenta lo que el docente marcó en cada evaluación; no se atribuyen los resultados a un apoyo '
                  'concreto.' + (f' En {lp.plural(supports["not_recorded"], "clase", "clases")} no se registraron '
                                 'los apoyos aplicados.' if supports['not_recorded'] else ''))
    else:
        doc.text('No se registraron apoyos aplicados durante las clases de este periodo.')

    # ----- Observations -----
    doc.heading('Observaciones del Docente')
    observed = [(e, evaluations[e.evaluation_id]) for e in entries
                if e.evaluation_id in evaluations and (evaluations[e.evaluation_id].observation or '').strip()]
    if observed:
        doc.table('Observaciones registradas después de cada clase', ['Fecha', 'Clase', 'Observación'],
                  [[timezone.localtime(e.date).strftime(DATE), class_name(e), evaluation.observation.strip()]
                   for e, evaluation in observed], (0.15, 0.27, 0.58))
    else:
        doc.text('El docente no registró observaciones en las clases de este periodo.')

    # ----- Recommendations -----
    doc.heading('Recomendaciones para las Próximas Clases')
    items = lp.recommendations(entries, subjects)
    for item in items:
        doc.text(f'{item["text"]} {item["reason"]}')
    if not items:
        doc.text('Con los datos registrados no surge ninguna recomendación por ahora.')
    doc.text(f'{NOT_A_DIAGNOSIS} Generado con Maestro Sombra el {timezone.localdate():{DATE}}.')
    doc.signatures('Firma del docente', 'Revisado por (DECE o coordinación)', keep=2)
    return doc.pdf()


def progress_file_name(student):
    """The download name: the student's pseudonym and the date, never their name (it may travel by e-mail)."""
    return f'informe-avance-{student.pseudonym}-{timezone.localdate():%Y-%m-%d}.pdf'
