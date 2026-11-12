"""Orchestrates the adaptation flow: read the worksheet, analyze it once, adapt it per student."""
import logging
import re
import threading
import time
import uuid

from django.db import close_old_connections, transaction
from django.db.models import Max

from apps.core.choices import RecordStatus
from apps.materials.models import MaterialStatus, OriginalMaterial
from apps.students.models import Student

from . import ai, figures
from .curriculum import SUBJECT_NAMES
from .models import CurricularAdaptation, LessonAdaptation, LessonPlan

logger = logging.getLogger('maestro_sombra')


def scrub_personal_data(text, student):
    """Removes the student's name from free text before it reaches the AI (LOPDP)."""
    if not text:
        return ''
    for word in sorted(student.name.split(), key=len, reverse=True):
        if len(word) > 2:
            text = re.sub(rf'\b{re.escape(word)}\b', 'el estudiante', text, flags=re.IGNORECASE)
    return text


def report(record, text):
    """What the AI is doing now, for the page the teacher is looking at (it polls the status)."""
    record.progress = text[:120]
    type(record).objects.filter(pk=record.pk).update(progress=record.progress)


def rules_by_student(adjustments):
    """{student id as text: [UDL rules]}: each student gets the rules chosen for them."""
    return {str(student_id): list(rules) for student_id, rules in (adjustments or {}).items()}


def _next_version(material, student, condition):
    current = (CurricularAdaptation.objects.filter(material=material, student=student, condition=condition)
               .aggregate(Max('version'))['version__max'])
    return (current or 0) + 1


def _create_adaptation(material, student, conditions, adjustments, *, source=None, simplify=False, feedback=''):
    started = time.monotonic()
    condition = conditions[0]  # groups the versions of the same worksheet
    content, model = ai.adapt_worksheet(
        analysis=material.ai_analysis, subject=material.subject.name, topic=material.topic,
        grade=student.grade, conditions=conditions, adjustments=adjustments,
        simplify=simplify, feedback=scrub_personal_data(feedback, student), subject_code=material.subject.code,
    )
    adaptation = CurricularAdaptation(
        material=material, student=student, condition=condition, conditions=list(conditions),
        version=_next_version(material, student, condition), source_adaptation=source,
        title=content.get('title', material.title)[:150], description=content.get('summary', ''),
        applied_adjustments=list(adjustments), content=content, ai_model=model[:50],
        generation_time_sec=round(time.monotonic() - started),
        color_filter=source.color_filter if source else CurricularAdaptation._meta.get_field('color_filter').default,
    )
    adaptation.full_clean(exclude=['code'])  # the code is generated on save
    adaptation.save()
    return adaptation


def material_mismatch(material, analysis):
    """Why the worksheet does not match the subject or topic the teacher wrote, or ''."""
    problems = []
    detected = analysis.get('detected_subject')
    if detected and detected not in ('OTRA', material.subject.code):
        problems.append(f'parece de {SUBJECT_NAMES.get(detected, detected)} y lo registraste en {material.subject.name}')
    if analysis.get('matches_topic') is False:
        why = (analysis.get('topic_mismatch') or '').strip().rstrip('.')
        problems.append(f'no trabaja el tema «{material.topic}»' + (f' ({why})' if why else ''))
    return ' y '.join(problems)


def process_material(material_id, requests, adjustments, *, session_text='', before_adapting=None,
                     check_topic=False, title_from_ai=False, progress=None):
    """
    Analyzes the worksheet and creates one adaptation per (student_id, conditions) request, each with the UDL
    rules chosen for that student ({student id: rules}). The material ends PROCESSED, or ERROR with a message.
    "Adaptar clase" passes the chosen session (session_text) so pages of another topic are rejected before
    adapting, and before_adapting(analysis), a step that may still stop the process. "Adaptar material" passes
    check_topic, so a worksheet of another subject or topic than the teacher wrote is stopped the same way.
    """
    material = OriginalMaterial.objects.select_related('subject').get(pk=material_id)
    material.status = MaterialStatus.PROCESSING
    material.ai_error = ''
    material.save(update_fields=['status', 'ai_error', 'updated_at'])
    say = progress or (lambda text: report(material, text))
    rules = rules_by_student(adjustments)
    try:
        started = time.monotonic()
        text, images = figures.load_sources(material)
        if not text.strip() and not images:
            raise ai.AIError('El archivo no tiene texto ni imágenes que leer. Revisa el archivo o pega el texto.')
        say('La IA está leyendo el material' + (f' ({len(images)} página{"s" if len(images) > 1 else ""})'
                                                 if len(images) > 1 else '') + '…')
        analysis, model = ai.analyze_material(text=text, image_parts=figures.image_parts(images),
                                              subject=material.subject.name, topic=material.topic,
                                              session_text=session_text, check_topic=check_topic)
        if session_text and analysis.get('matches_lesson') is False:
            raise ai.AIError(f'{ai.TOPIC_MISMATCH}: las páginas del libro son de otro tema. '
                             f'{analysis.get("lesson_mismatch", "")}'.strip())
        if check_topic and (mismatch := material_mismatch(material, analysis)):
            raise ai.AIError(f'{ai.MATERIAL_MISMATCH}: {mismatch}.')
        figures.crop_figures(material, images, analysis.get('figures', []))
        analysis['model'] = model
        analysis['seconds'] = round(time.monotonic() - started)
        material.ai_analysis = analysis
        fields = ['ai_analysis', 'updated_at']
        if title_from_ai and (analysis.get('title') or '').strip():
            material.title = analysis['title'].strip()[:150]
            fields.append('title')
        material.save(update_fields=fields)
        if before_adapting:
            before_adapting(analysis)
        students = {s.pk: s for s in Student.objects.filter(teacher=material.teacher, pk__in=[r[0] for r in requests])}
        with transaction.atomic():
            for number, (student_id, conditions) in enumerate(requests, start=1):
                say(f'Adaptando la ficha {number} de {len(requests)}…' if len(requests) > 1
                    else 'Adaptando la ficha…')
                _create_adaptation(material, students[student_id], conditions, rules.get(str(student_id), []))
        material.status = MaterialStatus.PROCESSED
        logger.info('Material %s processed: %s adaptation(s)', material.pk, len(requests))
    except ai.AIError as error:
        material.status = MaterialStatus.ERROR
        material.ai_error = str(error)
        logger.warning('Material %s failed: %s', material.pk, error)
    except Exception:  # noqa: BLE001 - the teacher must always get a final state, never "processing" forever
        material.status = MaterialStatus.ERROR
        material.ai_error = 'Ocurrió un error inesperado al procesar el material. Inténtalo de nuevo.'
        logger.exception('Unexpected error processing material %s', material.pk)
    material.progress = ''
    material.save(update_fields=['status', 'ai_error', 'progress', 'updated_at'])
    return material


def start_processing(material, requests, adjustments, *, check_topic=True):
    """
    Runs the processing in the background so the page can show the material status. The request is kept
    (student ids and rules, no personal data) so the teacher can run it again after a mismatch warning.
    """
    material.job = {'requests': [[str(student_id), list(conditions)] for student_id, conditions in requests],
                    'adjustments': rules_by_student(adjustments)}
    material.progress = 'Preparando el material…'
    material.save(update_fields=['job', 'progress', 'updated_at'])

    def run():
        try:
            process_material(material.pk, requests, adjustments, check_topic=check_topic, title_from_ai=True)
        finally:
            close_old_connections()

    threading.Thread(target=run, daemon=True).start()
    return material


def retry_without_topic_check(material):
    """The teacher says the worksheet is right after a mismatch warning: adapt it as it was requested."""
    job = material.job or {}
    requests = [(uuid.UUID(student_id), conditions) for student_id, conditions in job.get('requests', [])]
    if not requests:
        return False
    material.status = MaterialStatus.PENDING
    material.ai_error = ''
    material.save(update_fields=['status', 'ai_error', 'updated_at'])
    start_processing(material, requests, job.get('adjustments', {}), check_topic=False)
    return True


def regenerate(adaptation, *, simplify=True, feedback=''):
    """Creates the next version of an adaptation from the same analyzed worksheet."""
    target = _create_adaptation(
        adaptation.material, adaptation.student, adaptation.conditions or [adaptation.condition],
        adaptation.applied_adjustments,
        source=adaptation, simplify=simplify, feedback=feedback,
    )
    # The class keeps pointing to the newest version of its worksheet
    LessonAdaptation.objects.filter(worksheet=adaptation).update(worksheet=target)
    return target


def process_lesson(lesson_id, requests, adjustments, chosen_grade='', check_topic=True):
    """
    "Adaptar clase": reads the student book pages and checks they belong to the chosen session, reads
    the session (plus the teacher guide, also checked) once, and only then adapts the worksheet and the
    whole class for each student and all their conditions. check_topic=False when the teacher confirmed
    that the pages are right after a mismatch warning.
    """
    lesson = LessonPlan.objects.select_related('subject').get(pk=lesson_id)
    lesson.status = MaterialStatus.PROCESSING
    lesson.ai_error = ''
    lesson.save(update_fields=['status', 'ai_error', 'updated_at'])
    rules = rules_by_student(adjustments)

    def read_lesson(book_analysis):
        # Runs after the book is read and before anything is adapted: a guide of another topic stops here
        guide_parts = []
        report(lesson, 'La IA está leyendo la planificación' + (' y la guía del docente' if lesson.guide_file else '') + '…')
        if lesson.guide_file:
            with lesson.guide_file.open('rb') as guide:
                guide_parts = figures.image_parts(figures.file_images(guide.read()))
        analysis, _model = ai.analyze_lesson(
            session_text=lesson.session_text, guide_parts=guide_parts, book_summary=book_analysis.get('summary', ''),
            subject=lesson.subject.name, grade=class_grade, check_guide=check_topic, whole_week=lesson.is_week)
        if check_topic and guide_parts and analysis.get('guide_matches') is False:
            raise ai.AIError(f'{ai.TOPIC_MISMATCH}: la guía del docente es de otro tema. '
                             f'{analysis.get("guide_mismatch", "")}'.strip())
        lesson.analysis = analysis
        lesson.save(update_fields=['analysis', 'updated_at'])

    try:
        students = {s.pk: s for s in Student.objects.filter(teacher=lesson.teacher, pk__in=[r[0] for r in requests])}
        class_grade = students[requests[0][0]].grade
        material = process_material(lesson.material_id, requests, adjustments, before_adapting=read_lesson,
                                    session_text=lesson.session_text if check_topic else '',
                                    progress=lambda text: report(lesson, text.replace('el material', 'las páginas del libro')))
        if material.status == MaterialStatus.ERROR:
            raise ai.AIError(material.ai_error)
        book_summary = (material.ai_analysis or {}).get('summary', '')
        analysis = lesson.analysis
        with transaction.atomic():
            for number, (student_id, conditions) in enumerate(requests, start=1):
                student = students[student_id]
                report(lesson, f'Adaptando la clase {number} de {len(requests)}…' if len(requests) > 1
                       else 'Adaptando la clase…')
                content, model = ai.adapt_lesson(
                    lesson=analysis, book_summary=book_summary, subject=lesson.subject.name,
                    subject_code=lesson.subject.code, grade=student.grade, conditions=conditions,
                    adjustments=rules.get(str(student_id), []), chosen_grade=chosen_grade, whole_week=lesson.is_week,
                )
                worksheet = (material.adaptations.filter(student=student, condition=conditions[0])
                             .order_by('-version').first())
                LessonAdaptation.objects.update_or_create(lesson=lesson, student=student, defaults={
                    'condition': conditions[0], 'conditions': list(conditions),
                    'grade': chosen_grade or content['suggested_grade'],
                    'suggested_grade': content['suggested_grade'], 'worksheet': worksheet,
                    'content': content, 'ai_model': model[:50],
                })
        lesson.status = MaterialStatus.PROCESSED
        # The teacher's file (with the school's and people's names) is no longer needed: only its
        # anonymized session text and the adaptation are kept
        lesson.plan_file.delete(save=False)
        logger.info('Lesson %s adapted for %s student(s)', lesson.pk, len(requests))
    except ai.AIError as error:
        lesson.status = MaterialStatus.ERROR
        lesson.ai_error = str(error)
        logger.warning('Lesson %s failed: %s', lesson.pk, error)
    except Exception:  # noqa: BLE001 - the teacher must always get a final state
        lesson.status = MaterialStatus.ERROR
        lesson.ai_error = 'Ocurrió un error inesperado al adaptar la clase. Inténtalo de nuevo.'
        logger.exception('Unexpected error processing lesson %s', lesson.pk)
    lesson.progress = ''
    lesson.save(update_fields=['status', 'ai_error', 'plan_file', 'progress', 'updated_at'])
    return lesson


def start_lesson_processing(lesson, requests, adjustments, chosen_grade='', check_topic=True):
    report(lesson, 'Preparando la clase…')

    def run():
        try:
            process_lesson(lesson.pk, requests, adjustments, chosen_grade, check_topic)
        finally:
            close_old_connections()

    threading.Thread(target=run, daemon=True).start()
    return lesson


def _release_material(material, keep_lessons=True):
    """A material no active worksheet or class uses any more: its file and figures go (LOPDP)."""
    in_use = (material.adaptations.filter(record_status=RecordStatus.ACTIVE).exists()
              or (keep_lessons and material.lessons.filter(record_status=RecordStatus.ACTIVE).exists()))
    if in_use:
        return
    if material.file:
        material.file.delete(save=False)
    figures.delete_figures(material)
    material.record_status = RecordStatus.INACTIVE
    material.save(update_fields=['file', 'record_status', 'updated_at'])


@transaction.atomic
def delete_worksheet(adaptation):
    """
    Logical delete of a worksheet with all its versions (otherwise the previous version would show
    up in its place). The evaluations stay in the history; the material goes once nothing uses it.
    """
    versions = CurricularAdaptation.objects.filter(material=adaptation.material, student=adaptation.student,
                                                   condition=adaptation.condition)
    for version in versions:
        if version.pdf:
            version.pdf.delete(save=False)  # the PDF names the student
    LessonAdaptation.objects.filter(worksheet__in=versions).update(worksheet=None)
    count = versions.update(record_status=RecordStatus.INACTIVE, pdf='')
    _release_material(adaptation.material)
    return count


def discard_material(material):
    """Logical delete of a material's worksheets; its file and figures go (see _release_material)."""
    for worksheet in material.adaptations.filter(record_status=RecordStatus.ACTIVE):
        delete_worksheet(worksheet)
    _release_material(material, keep_lessons=False)


@transaction.atomic
def delete_lesson(lesson):
    """Logical delete of an adapted class, its worksheets and its stored plan and guide files."""
    for field in (lesson.plan_file, lesson.guide_file):
        if field:
            field.delete(save=False)
    lesson.record_status = RecordStatus.INACTIVE
    lesson.save(update_fields=['plan_file', 'guide_file', 'record_status', 'updated_at'])
    lesson.adaptations.update(record_status=RecordStatus.INACTIVE)
    if lesson.material:
        discard_material(lesson.material)
