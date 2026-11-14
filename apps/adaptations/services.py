"""Orchestrates the adaptation flow: read the worksheet, analyze it once, adapt it per student."""
import logging
import re
import threading
import time
import uuid
from datetime import timedelta

from django.db import close_old_connections, transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.core.choices import RecordStatus
from apps.materials.models import MaterialStatus, OriginalMaterial
from apps.students.models import Student, ai_profile_allowed

from . import ai, document_privacy, figures, learning_progress, pedagogical_relevance
from .curriculum import SUBJECT_NAMES
from .models import CurricularAdaptation, LessonAdaptation, LessonPlan

logger = logging.getLogger('maestro_sombra')


def scrub_personal_data(text, student):
    """
    Removes the student's name, and any identifier (ID numbers, phones, e-mails...), from free text before it
    reaches the AI (LOPDP): the teacher's feedback and classroom observations.
    """
    if not text:
        return ''
    for word in sorted(student.name.split(), key=len, reverse=True):
        if len(word) > 2:
            text = re.sub(rf'\b{re.escape(word)}\b', 'el estudiante', text, flags=re.IGNORECASE)
    return document_privacy.sanitize_text(text)[0]


def scrub_names(text, names):
    """The text without the given students' names nor other identifiers, before it reaches the AI (LOPDP)."""
    if not text:
        return text or ''
    return document_privacy.sanitize_text(text, names)[0]


def teacher_student_names(teacher):
    return [student.name for student in Student.objects.filter(teacher=teacher)]


def scrubbed_analysis(analysis, names):
    """A copy of the worksheet reading without the names of the teacher's students (a photo may show one)."""
    analysis = dict(analysis or {})
    analysis['extracted_text'] = scrub_names(analysis.get('extracted_text', ''), names)
    analysis['handwritten'] = [scrub_names(str(item), names) for item in analysis.get('handwritten') or []]
    analysis['figures'] = [{**figure, 'description': scrub_names(figure.get('description', ''), names)}
                           for figure in analysis.get('figures') or []]
    return analysis


def student_profile(student):
    """The individual pedagogical profile, read only in this server by the relevance engine."""
    return {field: getattr(student, field, '') or '' for field in
            ('learning_strengths', 'learning_barriers', 'pedagogical_supports')}


def worksheet_relevance(material, analysis, student, conditions, adjustments):
    figures_text = ' '.join(f.get('description', '') for f in analysis.get('figures') or [])
    return pedagogical_relevance.analyze(
        conditions=conditions, profile=student_profile(student), subject_code=material.subject.code,
        subject_name=material.subject.name, topic=material.topic,
        activity_text=f"{analysis.get('extracted_text', '')} {figures_text}",
        exercise_count=analysis.get('exercise_count') or 0, has_figures=bool(analysis.get('figures')),
        adjustments=adjustments, share_profile=ai_profile_allowed(student))


def report(record, text):
    """
    What the AI is doing now, for the page the teacher is looking at (it polls the status). It also refreshes
    updated_at: a job that stops reporting for STALE_AFTER is taken as interrupted (see claim_for_retry).
    """
    record.progress = text[:120]
    type(record).objects.filter(pk=record.pk).update(progress=record.progress, updated_at=timezone.now())


# A job with no news for this long was interrupted (the server stopped while its thread ran): it may be resumed.
# Longer than the slowest single step: an AI call goes through at most 3 models, 2 tries each, 60 s timeout.
STALE_AFTER = timedelta(minutes=15)
WORKING = (MaterialStatus.PENDING, MaterialStatus.PROCESSING)


def is_interrupted(record):
    """Still "working" but with no news for STALE_AFTER: its background job is no longer running."""
    return record.status in WORKING and record.updated_at < timezone.now() - STALE_AFTER


def claim_for_retry(record):
    """
    Reserves a material or a class for one retry with a single conditional UPDATE in PostgreSQL: it moves to
    PENDING only if it is still in ERROR, or interrupted. Of two simultaneous "Reintentar" requests only one
    gets the row; the other starts nothing (no second background job, no duplicate calls to the AI).
    """
    now = timezone.now()
    available = Q(status=MaterialStatus.ERROR) | Q(status__in=WORKING, updated_at__lt=now - STALE_AFTER)
    claimed = type(record).objects.filter(available, pk=record.pk).update(
        status=MaterialStatus.PENDING, ai_error='', updated_at=now)
    if claimed:
        record.status, record.ai_error, record.updated_at = MaterialStatus.PENDING, '', now
    return bool(claimed)


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
    analysis = scrubbed_analysis(material.ai_analysis, teacher_student_names(material.teacher))
    # The relevance engine reads the profile here; the AI only gets its authorized part (no profile)
    relevance = worksheet_relevance(material, analysis, student, conditions, adjustments)
    content, model = ai.adapt_worksheet(
        analysis=analysis, subject=material.subject.name, topic=material.topic,
        grade=student.grade, conditions=conditions, adjustments=adjustments,
        simplify=simplify, feedback=scrub_personal_data(feedback, student), subject_code=material.subject.code,
        relevance=relevance,
    )
    content['relevance'] = pedagogical_relevance.storable(relevance)
    content['review_checks'] = pedagogical_relevance.review_checks(
        content, kind='worksheet', conditions=conditions, student_name=student.name, original=analysis)
    # The student's related history, summarized here for the teacher's review. Added after the AI answered:
    # it is never part of the request (the current consent does not cover sharing past evaluations)
    content['learning_history'] = learning_progress.adaptation_context(student, material.subject.code,
                                                                       topic=material.topic)
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
                     check_topic=False, title_from_ai=False, progress=None, resume=False):
    """
    Analyzes the worksheet and creates one adaptation per (student_id, conditions) request, each with the UDL
    rules chosen for that student ({student id: rules}). The material ends PROCESSED, or ERROR with a message.
    "Adaptar clase" passes the chosen session (session_text) so pages of another topic are rejected before
    adapting, and before_adapting(analysis), a step that may still stop the process. "Adaptar material" passes
    check_topic, so a worksheet of another subject or topic than the teacher wrote is stopped the same way.
    Every worksheet is saved as soon as it is ready: with resume, what was already done (the reading of the
    material, the worksheets of some students) is kept and only the rest is asked to the AI.
    """
    material = OriginalMaterial.objects.select_related('subject').get(pk=material_id)
    material.status = MaterialStatus.PROCESSING
    material.ai_error = ''
    material.save(update_fields=['status', 'ai_error', 'updated_at'])
    say = progress or (lambda text: report(material, text))
    rules = rules_by_student(adjustments)
    try:
        if resume and (material.ai_analysis or {}).get('extracted_text'):
            analysis = material.ai_analysis  # already read in the attempt that stopped
        else:
            analysis = read_material(material, say, session_text, check_topic, title_from_ai,
                                     private_names=teacher_student_names(material.teacher))
        if before_adapting:
            before_adapting(analysis)
        students = {s.pk: s for s in Student.objects.filter(teacher=material.teacher, pk__in=[r[0] for r in requests])}
        done = set(material.adaptations.filter(record_status=RecordStatus.ACTIVE).values_list('student_id', flat=True))
        for number, (student_id, conditions) in enumerate(requests, start=1):
            if student_id in done:
                continue
            say(f'Adaptando la ficha {number} de {len(requests)}…' if len(requests) > 1 else 'Adaptando la ficha…')
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


def read_material(material, say, session_text, check_topic, title_from_ai, private_names=()):
    """
    The AI reads the worksheet once; it is saved at once, so a later failure does not lose it. The names of the
    teacher's students are removed from a typed or Word text before it is sent, and from what the AI read.
    """
    started = time.monotonic()
    # Privacy check BEFORE sending: only the cleaned text and redacted pages leave; a blocked file stops here
    inspection = document_privacy.inspect_material(material, private_names)
    if not inspection.allowed:
        raise ai.AIError(f'{ai.PRIVACY_BLOCK}: {inspection.message}')
    text, images = inspection.text, inspection.images
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
    analysis = scrubbed_analysis(analysis, private_names)
    figures.crop_figures(material, images, analysis.get('figures', []))
    analysis['model'] = model
    analysis['seconds'] = round(time.monotonic() - started)
    # What the check did, for the teacher (counts only, never the removed values)
    analysis['privacy'] = {'status': inspection.status, 'message': inspection.message}
    material.ai_analysis = analysis
    fields = ['ai_analysis', 'updated_at']
    if title_from_ai and (analysis.get('title') or '').strip():
        material.title = analysis['title'].strip()[:150]
        fields.append('title')
    material.save(update_fields=fields)
    return analysis


def job(requests, adjustments, **options):
    """The request kept with the material (student ids, NEE and rules: no personal data) to resume it later."""
    return {'requests': [[str(student_id), list(conditions)] for student_id, conditions in requests],
            'adjustments': rules_by_student(adjustments), **options}


def job_requests(saved):
    return [(uuid.UUID(student_id), conditions) for student_id, conditions in (saved or {}).get('requests', [])]


def start_processing(material, requests, adjustments, *, check_topic=True, resume=False):
    """
    Runs the processing in the background so the page can show the material status. The request is kept
    (student ids and rules, no personal data) so the teacher can run it again after a mismatch warning.
    """
    material.job = job(requests, adjustments, check_topic=check_topic)
    material.progress = 'Preparando el material…'
    material.save(update_fields=['job', 'progress', 'updated_at'])

    def run():
        try:
            process_material(material.pk, requests, adjustments, check_topic=check_topic, title_from_ai=True,
                             resume=resume)
        finally:
            close_old_connections()

    threading.Thread(target=run, daemon=True).start()
    return material


def retry_without_topic_check(material):
    """The teacher says the worksheet is right after a mismatch warning: adapt it as it was requested."""
    requests = job_requests(material.job)
    if not requests or not claim_for_retry(material):
        return False
    start_processing(material, requests, material.job.get('adjustments', {}), check_topic=False)
    return True


def resume_material(material):
    """'Reintentar' after the AI failed: keeps what was done and asks the AI only for what is missing."""
    requests = job_requests(material.job)
    if not requests or not claim_for_retry(material):  # one retry at a time, even with two clicks
        return False
    start_processing(material, requests, material.job.get('adjustments', {}),
                     check_topic=material.job.get('check_topic', True), resume=True)
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


def process_lesson(lesson_id, requests, adjustments, chosen_grade='', check_topic=True, resume=False):
    """
    "Adaptar clase": reads the student book pages and checks they belong to the chosen session, reads
    the session (plus the teacher guide, also checked) once, and only then adapts the worksheet and the
    whole class for each student and all their conditions. check_topic=False when the teacher confirmed
    that the pages are right after a mismatch warning. Each step is saved when it is done; with resume, a class
    that stopped (Google overloaded) continues from the step that failed.
    """
    lesson = LessonPlan.objects.select_related('subject').get(pk=lesson_id)
    lesson.status = MaterialStatus.PROCESSING
    lesson.ai_error = ''
    lesson.save(update_fields=['status', 'ai_error', 'updated_at'])
    rules = rules_by_student(adjustments)
    names = teacher_student_names(lesson.teacher)
    # The plan's text was cleaned when it was read; it is checked again for any identifier before it is sent
    session_text = document_privacy.sanitize_text(lesson.session_text, names)[0]

    def read_lesson(book_analysis):
        # Runs after the book is read and before anything is adapted: a guide of another topic stops here
        if resume and lesson.analysis:
            return
        guide_parts, guide_note = [], ''
        report(lesson, 'La IA está leyendo la planificación' + (' y la guía del docente' if lesson.guide_file else '') + '…')
        if lesson.guide_file:
            with lesson.guide_file.open('rb') as guide:
                inspection = document_privacy.inspect_file(guide.read(), names)
            if inspection.allowed:
                guide_parts = figures.image_parts(inspection.images)
            else:  # the class goes on without the guide: it is optional, and it is never sent unchecked
                guide_note = f'La guía del docente no se envió a la IA: {inspection.message}'
        analysis, _model = ai.analyze_lesson(
            session_text=session_text, guide_parts=guide_parts, book_summary=book_analysis.get('summary', ''),
            subject=lesson.subject.name, grade=class_grade, check_guide=check_topic, whole_week=lesson.is_week)
        if check_topic and guide_parts and analysis.get('guide_matches') is False:
            raise ai.AIError(f'{ai.TOPIC_MISMATCH}: la guía del docente es de otro tema. '
                             f'{analysis.get("guide_mismatch", "")}'.strip())
        if guide_note:
            analysis['privacy_note'] = guide_note
        lesson.analysis = analysis
        lesson.save(update_fields=['analysis', 'updated_at'])

    try:
        students = {s.pk: s for s in Student.objects.filter(teacher=lesson.teacher, pk__in=[r[0] for r in requests])}
        class_grade = students[requests[0][0]].grade
        material = process_material(lesson.material_id, requests, adjustments, before_adapting=read_lesson,
                                    session_text=session_text if check_topic else '', resume=resume,
                                    progress=lambda text: report(lesson, text.replace('el material', 'las páginas del libro')))
        if material.status == MaterialStatus.ERROR:
            raise ai.AIError(material.ai_error)
        book_summary = (material.ai_analysis or {}).get('summary', '')
        analysis = lesson.analysis
        done = set(lesson.adaptations.values_list('student_id', flat=True)) if resume else set()
        for number, (student_id, conditions) in enumerate(requests, start=1):
            if student_id in done:
                continue
            student = students[student_id]
            report(lesson, f'Adaptando la clase {number} de {len(requests)}…' if len(requests) > 1
                   else 'Adaptando la clase…')
            student_rules = rules.get(str(student_id), [])
            relevance = lesson_relevance(lesson, analysis, book_summary, student, conditions, student_rules)
            content, model = ai.adapt_lesson(
                lesson=analysis, book_summary=book_summary, subject=lesson.subject.name,
                subject_code=lesson.subject.code, grade=student.grade, conditions=conditions,
                adjustments=student_rules, chosen_grade=chosen_grade, whole_week=lesson.is_week,
                relevance=relevance,
            )
            content['relevance'] = pedagogical_relevance.storable(relevance)
            content['review_checks'] = pedagogical_relevance.review_checks(
                content, kind='lesson', conditions=conditions, student_name=student.name, original=analysis)
            content['learning_history'] = learning_progress.adaptation_context(  # local only, after the AI call
                student, lesson.subject.code, skill=analysis.get('skill', ''), topic=lesson.title)
            worksheet = (material.adaptations.filter(student=student, condition=conditions[0])
                         .order_by('-version').first())
            # Saved at once: if the next student fails, this one is kept for the retry
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


def lesson_relevance(lesson, analysis, book_summary, student, conditions, adjustments):
    """The relevance engine for a whole class: its moments, objective, skill and the book pages it uses."""
    moments = ' '.join(f"{m.get('name', '')} {m.get('activities', '')}" for m in analysis.get('moments', []))
    return pedagogical_relevance.analyze(
        conditions=conditions, profile=student_profile(student), subject_code=lesson.subject.code,
        subject_name=lesson.subject.name, topic=analysis.get('title') or lesson.title,
        activity_text=f'{moments} {book_summary}', is_lesson=True,
        objective=analysis.get('objective', ''), skill=analysis.get('skill', ''), adjustments=adjustments,
        share_profile=ai_profile_allowed(student))


def start_lesson_processing(lesson, requests, adjustments, chosen_grade='', check_topic=True, resume=False):
    # The request is kept with the book pages' material, so "Reintentar" can continue it
    OriginalMaterial.objects.filter(pk=lesson.material_id).update(
        job=job(requests, adjustments, chosen_grade=chosen_grade, check_topic=check_topic))
    report(lesson, 'Retomando la clase…' if resume else 'Preparando la clase…')

    def run():
        try:
            process_lesson(lesson.pk, requests, adjustments, chosen_grade, check_topic, resume)
        finally:
            close_old_connections()

    threading.Thread(target=run, daemon=True).start()
    return lesson


def resume_lesson(lesson):
    """'Reintentar' a class that stopped: the pages, the plan and the students already done are kept."""
    saved = lesson.material.job if lesson.material else None
    requests = job_requests(saved)
    if not requests or not lesson.plan_file or not claim_for_retry(lesson):  # one retry at a time
        return False
    start_lesson_processing(lesson, requests, saved.get('adjustments', {}), saved.get('chosen_grade', ''),
                            saved.get('check_topic', True), resume=True)
    return True


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
