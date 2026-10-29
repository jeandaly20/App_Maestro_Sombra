"""Orchestrates the adaptation flow: read the worksheet, analyze it once, adapt it per student."""
import logging
import re
import threading
import time

from django.db import close_old_connections, transaction
from django.db.models import Max

from apps.materials.models import MaterialStatus, OriginalMaterial
from apps.students.models import Student

from . import ai, figures
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


def _next_version(material, student, condition):
    current = (CurricularAdaptation.objects.filter(material=material, student=student, condition=condition)
               .aggregate(Max('version'))['version__max'])
    return (current or 0) + 1


def _create_adaptation(material, student, condition, adjustments, *, source=None, simplify=False, feedback=''):
    started = time.monotonic()
    content, model = ai.adapt_worksheet(
        analysis=material.ai_analysis, subject=material.subject.name, topic=material.topic,
        grade=student.grade, condition=condition, adjustments=adjustments,
        simplify=simplify, feedback=scrub_personal_data(feedback, student), subject_code=material.subject.code,
    )
    adaptation = CurricularAdaptation(
        material=material, student=student, condition=condition,
        version=_next_version(material, student, condition), source_adaptation=source,
        title=content.get('title', material.title)[:150], description=content.get('summary', ''),
        applied_adjustments=list(adjustments), content=content, ai_model=model[:50],
        generation_time_sec=round(time.monotonic() - started),
        color_filter=source.color_filter if source else CurricularAdaptation._meta.get_field('color_filter').default,
    )
    adaptation.full_clean(exclude=['code'])  # the code is generated on save
    adaptation.save()
    return adaptation


def process_material(material_id, requests, adjustments):
    """
    Analyzes the worksheet and creates one adaptation per (student_id, condition) request.
    The material ends PROCESSED, or ERROR with a message for the teacher.
    """
    material = OriginalMaterial.objects.select_related('subject').get(pk=material_id)
    material.status = MaterialStatus.PROCESSING
    material.ai_error = ''
    material.save(update_fields=['status', 'ai_error', 'updated_at'])
    try:
        started = time.monotonic()
        text, images = figures.load_sources(material)
        if not text.strip() and not images:
            raise ai.AIError('El archivo no tiene texto ni imágenes que leer. Revisa el archivo o pega el texto.')
        analysis, model = ai.analyze_material(text=text, image_parts=figures.image_parts(images),
                                              subject=material.subject.name, topic=material.topic)
        figures.crop_figures(material, images, analysis.get('figures', []))
        analysis['model'] = model
        analysis['seconds'] = round(time.monotonic() - started)
        material.ai_analysis = analysis
        material.save(update_fields=['ai_analysis', 'updated_at'])
        students = {s.pk: s for s in Student.objects.filter(teacher=material.teacher, pk__in=[r[0] for r in requests])}
        with transaction.atomic():
            for student_id, condition in requests:
                _create_adaptation(material, students[student_id], condition, adjustments)
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
    material.save(update_fields=['status', 'ai_error', 'updated_at'])
    return material


def start_processing(material, requests, adjustments):
    """Runs the processing in the background so the page can show the material status."""
    def run():
        try:
            process_material(material.pk, requests, adjustments)
        finally:
            close_old_connections()

    threading.Thread(target=run, daemon=True).start()
    return material


def regenerate(adaptation, *, simplify=True, feedback=''):
    """Creates the next version of an adaptation from the same analyzed worksheet."""
    return _create_adaptation(
        adaptation.material, adaptation.student, adaptation.condition, adaptation.applied_adjustments,
        source=adaptation, simplify=simplify, feedback=feedback,
    )


def process_lesson(lesson_id, requests, adjustments, chosen_grade=''):
    """
    "Adaptar clase": adapts the student book pages (as any worksheet), reads the class plan session
    (plus the teacher guide) once, and adapts the whole class for each (student, condition).
    """
    lesson = LessonPlan.objects.select_related('subject').get(pk=lesson_id)
    lesson.status = MaterialStatus.PROCESSING
    lesson.ai_error = ''
    lesson.save(update_fields=['status', 'ai_error', 'updated_at'])
    try:
        material = process_material(lesson.material_id, requests, adjustments)
        if material.status == MaterialStatus.ERROR:
            raise ai.AIError(material.ai_error)
        book_summary = (material.ai_analysis or {}).get('summary', '')
        guide_parts = []
        if lesson.guide_file:
            with lesson.guide_file.open('rb') as guide:
                guide_parts = figures.image_parts(figures.file_images(guide.read()))
        students = {s.pk: s for s in Student.objects.filter(teacher=lesson.teacher, pk__in=[r[0] for r in requests])}
        class_grade = students[requests[0][0]].grade
        analysis, model = ai.analyze_lesson(session_text=lesson.session_text, guide_parts=guide_parts,
                                            book_summary=book_summary, subject=lesson.subject.name, grade=class_grade)
        lesson.analysis = analysis
        lesson.save(update_fields=['analysis', 'updated_at'])
        with transaction.atomic():
            for student_id, condition in requests:
                student = students[student_id]
                content, model = ai.adapt_lesson(
                    lesson=analysis, book_summary=book_summary, subject=lesson.subject.name,
                    subject_code=lesson.subject.code, grade=student.grade, condition=condition,
                    adjustments=adjustments, chosen_grade=chosen_grade,
                )
                worksheet = (material.adaptations.filter(student=student, condition=condition)
                             .order_by('-version').first())
                LessonAdaptation.objects.update_or_create(lesson=lesson, student=student, defaults={
                    'condition': condition, 'grade': chosen_grade or content['suggested_grade'],
                    'suggested_grade': content['suggested_grade'], 'worksheet': worksheet,
                    'content': content, 'ai_model': model[:50],
                })
        lesson.status = MaterialStatus.PROCESSED
        logger.info('Lesson %s adapted for %s student(s)', lesson.pk, len(requests))
    except ai.AIError as error:
        lesson.status = MaterialStatus.ERROR
        lesson.ai_error = str(error)
        logger.warning('Lesson %s failed: %s', lesson.pk, error)
    except Exception:  # noqa: BLE001 - the teacher must always get a final state
        lesson.status = MaterialStatus.ERROR
        lesson.ai_error = 'Ocurrió un error inesperado al adaptar la clase. Inténtalo de nuevo.'
        logger.exception('Unexpected error processing lesson %s', lesson.pk)
    lesson.save(update_fields=['status', 'ai_error', 'updated_at'])
    return lesson


def start_lesson_processing(lesson, requests, adjustments, chosen_grade=''):
    def run():
        try:
            process_lesson(lesson.pk, requests, adjustments, chosen_grade)
        finally:
            close_old_connections()

    threading.Thread(target=run, daemon=True).start()
    return lesson
