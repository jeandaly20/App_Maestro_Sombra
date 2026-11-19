"""
The report of one finished classroom evaluation, as a PDF for the school's paperwork (DECE, coordination), in the
APA 7 style of every document for the institution (core.apa).

It gathers what the app already knows about that class for that student: the class and its curriculum, the
adaptation that was used, the results the teacher registered, the supports, the teacher's observation and the
recommendations. It is built here, on the server, and only for the student's own teacher; nothing of it is sent
to the AI. It describes what was observed: it is not a diagnosis.
"""
from django.utils import timezone

from apps.adaptations.models import LessonAdaptation
from apps.core.apa import ApaDocument
from apps.core.choices import RecordStatus

from .models import SUPPORT_CHOICES

TITLE = 'Informe de Evaluación de la Clase Adaptada'
CONFIDENTIAL = ('Este documento es confidencial: contiene datos personales de un estudiante con necesidades educativas '
                'específicas, protegidos por la Ley Orgánica de Protección de Datos Personales (LOPDP). Debe '
                'entregarse solo a quien corresponda dentro de la institución.')
NOT_A_DIAGNOSIS = ('Este informe describe lo observado y registrado por el docente después de la clase. No es un '
                   'diagnóstico ni atribuye los resultados a un apoyo concreto.')
NOT_RECORDED = 'No registrado'
SUPPORT_LABELS = dict(SUPPORT_CHOICES)
# Section heading -> the title of its table (APA: every table is numbered and named)
CAPTIONS = {
    'Datos Generales': 'Datos del estudiante y de la clase evaluada',
    'Currículo y Adaptación Aplicada': 'Currículo de la clase y adaptación curricular aplicada',
    'Adaptaciones por Ejercicio': 'Adaptaciones realizadas en cada ejercicio de la guía',
    'Resultados de la Evaluación': 'Resultados registrados por el docente al finalizar la clase',
    'Apoyos y Observaciones del Docente': 'Apoyos aplicados y observaciones del docente',
}


def lesson_of(adaptation):
    """The adapted class this worksheet belongs to (same student and book pages), or None for a loose worksheet."""
    return (LessonAdaptation.objects
            .filter(student=adaptation.student, lesson__material=adaptation.material,
                    record_status=RecordStatus.ACTIVE)
            .select_related('lesson').order_by('-created_at').first())


def _label(evaluation, field):
    return getattr(evaluation, f'get_{field}_display')() if getattr(evaluation, field) else NOT_RECORDED


def report_sections(evaluation):
    """
    [(heading, kind, rows)]: 'fields' rows are (label, value, bold); 'table' rows start with their header row;
    'text' rows are paragraphs (label, text, bold).
    """
    adaptation = evaluation.adaptation
    student, material = adaptation.student, adaptation.material
    lesson_adaptation = lesson_of(adaptation)
    lesson = lesson_adaptation.lesson if lesson_adaptation else None
    analysis = (lesson.analysis or {}) if lesson else {}
    lesson_content = (lesson_adaptation.content or {}) if lesson_adaptation else {}
    content = adaptation.content or {}
    class_name = ' · '.join(part for part in (lesson.session_label if lesson else '', lesson.title if lesson else '')
                            if part) or material.topic

    sections = [
        ('Datos Generales', 'fields', [
            ('Docente', material.teacher.full_name, False),
            ('Estudiante', student.name, True),
            ('Grado', student.get_grade_display(), False),
            ('Necesidades educativas específicas', adaptation.conditions_label, False),
            ('Asignatura', material.subject.name, False),
            ('Clase', class_name, True),
            ('Ficha adaptada', f'{adaptation.code} · versión {adaptation.version} · {adaptation.title}', False),
            ('Fecha de la evaluación', timezone.localtime(evaluation.updated_at).strftime('%d/%m/%Y'), False),
        ]),
    ]
    curriculum = [
        ('Destreza con criterio de desempeño', analysis.get('skill', ''), False),
        ('Indicador de evaluación', analysis.get('indicator', ''), False),
        ('Objetivo de la clase', analysis.get('objective', ''), False),
        ('Objetivo adaptado', lesson_content.get('adapted_objective') or content.get('learning_goal', ''), True),
        ('Grado de adaptación', lesson_adaptation.get_grade_display() if lesson_adaptation else '', False),
        ('Ajustes DUA de la ficha', ', '.join(adaptation.adjustment_labels), False),
        ('Adaptación curricular', ' / '.join(lesson_content.get('plan_row') or []), False),
    ]
    sections.append(('Currículo y Adaptación Aplicada', 'fields',
                     [(label, value, bold) for label, value, bold in curriculum if str(value).strip()]))
    exercises = content.get('exercise_adaptations') or []
    if exercises:
        sections.append(('Adaptaciones por Ejercicio', 'table', [
            ['Ejercicio de la guía', 'Barrera para el estudiante', 'Qué cambió en la ficha', 'Apoyo del docente sombra'],
            *[[item.get('exercise', ''), item.get('barrier', ''),
               f'{item.get("action_label") or item.get("action", "")}: {item.get("adaptation", "")}',
               item.get('teacher_support', '')] for item in exercises],
        ]))
    sections.append(('Resultados de la Evaluación', 'fields', [
        ('Logro del objetivo adaptado', _label(evaluation, 'goal_attainment'), True),
        ('Escala del Ministerio de Educación', _label(evaluation, 'achievement_level'), True),
        ('Comprensión', _label(evaluation, 'comprehension'), False),
        ('Nivel de apoyo que necesitó', _label(evaluation, 'autonomy_level'), False),
        ('Sobrecarga cognitiva', _label(evaluation, 'overload'), False),
        ('Pidió una versión más simple', 'Sí' if evaluation.regeneration_requested else 'No', False),
    ]))
    applied = [SUPPORT_LABELS.get(key, key) for key in evaluation.supports_applied]
    useful = [SUPPORT_LABELS.get(key, key) for key in evaluation.supports_useful]
    sections.append(('Apoyos y Observaciones del Docente', 'fields', [
        ('Apoyos aplicados durante la clase', ', '.join(applied) or NOT_RECORDED, False),
        ('Apoyos que resultaron útiles', ', '.join(useful) or NOT_RECORDED, False),
        ('Descripción de los apoyos', evaluation.supports_note or NOT_RECORDED, False),
        ('Observación del docente', evaluation.observation or NOT_RECORDED, False),
    ]))
    recommendations = [item.get('text', '') for item in evaluation.local_recommendations or [] if item.get('text')]
    if evaluation.recommendation_source == 'AI' and evaluation.ai_recommendation.strip():
        recommendations.insert(0, evaluation.ai_recommendation.strip())
    sections.append(('Recomendaciones para la Próxima Clase', 'text', [
        (f'Recomendación {number}', text, False) for number, text in enumerate(recommendations, 1)
    ] or [('Recomendaciones', 'No se registraron recomendaciones.', False)]))
    return sections


def evaluation_report_pdf(evaluation):
    """The report as PDF bytes (A4, APA 7), with signature lines at the end."""
    doc = ApaDocument(TITLE, f'{evaluation.adaptation.code} · {timezone.localtime(evaluation.updated_at):%d/%m/%Y}')
    doc.text(CONFIDENTIAL)
    for heading, kind, rows in report_sections(evaluation):
        doc.heading(heading)
        if kind == 'table':
            doc.table(CAPTIONS[heading], rows[0], rows[1:], (0.22, 0.24, 0.30, 0.24))
        elif kind == 'fields':
            doc.fields(CAPTIONS[heading], [(label, value) for label, value, _bold in rows])
        else:
            for _label, text, _bold in rows:
                doc.text(text)
    doc.text(f'{NOT_A_DIAGNOSIS} Generado con Maestro Sombra el {timezone.localdate():%d/%m/%Y}.')
    doc.signatures('Firma del docente', 'Revisado por (DECE o coordinación)', keep=2)
    return doc.pdf()


def report_file_name(evaluation):
    """The download name: the worksheet code and the date, never the student's name (it may travel by e-mail)."""
    adaptation = evaluation.adaptation
    return f'informe-evaluacion-{adaptation.code}-{timezone.localtime(evaluation.updated_at):%Y-%m-%d}.pdf'
