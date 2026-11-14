import base64
import logging
import re
import zipfile
from io import BytesIO
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.staticfiles import finders
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views import View
from django.views.generic import FormView, TemplateView
from PIL import Image
from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from xhtml2pdf import default as pisa_default
from xhtml2pdf import pisa

from apps.core.choices import GRADE_NUMBERS, AdaptationGrade, ColorFilter, RecordStatus, UdlAdjustment
from apps.evaluations.models import Evaluation, EvaluationStatus, Outcome
from apps.materials.models import InputType, MaterialStatus, OriginalMaterial

from . import ai, figures, lesson_plan, nee_modules, services, visuals
from .curriculum import curriculum_warnings
from .forms import ACCEPT_BY_TYPE, AdaptMaterialForm, LessonSetupForm, LessonUploadForm
from .models import AdaptationStatus, CurricularAdaptation, LessonAdaptation, LessonPlan

logger = logging.getLogger('maestro_sombra')

PDF_BACKGROUNDS = {
    ColorFilter.NONE: '#ffffff', ColorFilter.CREAM: '#fbf3dc', ColorFilter.BLUE: '#e3eefb', ColorFilter.GREEN: '#e6f4e8',
}


def own_material(request, pk):
    # A teacher can only open their own materials; anyone else gets a 404
    return get_object_or_404(OriginalMaterial.objects.select_related('subject'), pk=pk, teacher=request.user,
                             record_status=RecordStatus.ACTIVE)


def own_adaptation(request, pk):
    return get_object_or_404(
        CurricularAdaptation.objects.select_related('material__subject', 'student'),
        pk=pk, material__teacher=request.user, record_status=RecordStatus.ACTIVE,
    )


def back_to(request, fallback):
    """Where to return after a worksheet action: the page that sent it (e.g. its class), if it is ours."""
    target = request.POST.get('next') or request.GET.get('next') or ''
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                       require_https=request.is_secure()):
        return target
    return fallback


def figure_image_path(material, number):
    """Storage path of a cropped original figure (1-based), or None."""
    figure_list = (material.ai_analysis or {}).get('figures', [])
    if not 1 <= number <= len(figure_list):
        return None
    path = figure_list[number - 1].get('image')
    if path and figures.figure_path_is_safe(path, material) and default_storage.exists(path):
        return path
    return None


def fit_size(size, max_width=330, max_height=200):
    """Display size (points) that keeps the proportions inside the PDF box."""
    width, height = size
    scale = min(max_width / width, max_height / height, 1.5)
    return round(width * scale), round(height * scale)


def items_with_images(adaptation, key, *, embed=False):
    """
    Each concept or step (content[key]) plus the image it shows: the original figure it uses
    (cropped from the worksheet) or a support drawn by the system. embed=True returns data: URIs for the PDF.
    """
    material = adaptation.material
    figure_list = (material.ai_analysis or {}).get('figures', [])
    steps = []
    for step in adaptation.content.get(key, []):
        step = dict(step)
        number = step.get('figure_number') or 0
        path = figure_image_path(material, number)
        if path:
            if embed:
                with default_storage.open(path, 'rb') as image:
                    data = image.read()
                step['figure_src'] = 'data:image/png;base64,' + base64.b64encode(data).decode()
                step['figure_width'], step['figure_height'] = fit_size(Image.open(BytesIO(data)).size)
            else:
                step['figure_src'] = reverse('material_figure', args=[material.pk, number])
            if not step.get('figure_description'):
                step['figure_description'] = figure_list[number - 1].get('description', '')
        visual = visuals.clean_visual(step.get('visual'))
        if visual:
            step['visual_src'] = visuals.data_uri(visual)
            step['visual_label'] = visuals.TYPE_LABELS[visual['type']]
        steps.append(step)
    return steps


def steps_with_images(adaptation, *, embed=False):
    return items_with_images(adaptation, 'steps', embed=embed)


def concepts_with_images(adaptation, *, embed=False):
    return items_with_images(adaptation, 'concepts', embed=embed)


class AdaptView(LoginRequiredMixin, FormView):
    """Step 1: upload the worksheet and choose the students."""
    template_name = 'adaptations/adapt_form.html'
    form_class = AdaptMaterialForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['teacher'] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        if self.request.GET.get('alumno'):
            initial['students'] = [self.request.GET['alumno']]
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context['form']
        context.update({
            'accept_by_type': ACCEPT_BY_TYPE,
            'student_options': [
                {'option': option, 'student': form.student_objects[option.data['value']]}
                for option in form['students']
            ],
        })
        return context

    def form_valid(self, form):
        data = form.cleaned_data
        file = data.get('file') if data['input_type'] != 'TEXT' else None
        material = OriginalMaterial(
            # Until the AI reads the worksheet's own title, it is known by its topic
            teacher=self.request.user, subject=data['subject'], title=data['topic'], topic=data['topic'],
            input_type=data['input_type'], input_text=data.get('input_text', '') if not file else '',
        )
        if file:
            material.file = file
            material.file_name = Path(file.name).name[:255]
            material.size_kb = max(1, round(file.size / 1024))
        material.full_clean()
        material.save()
        logger.info('Material %s uploaded by teacher=%s (%s)', material.pk, self.request.user.pk, material.input_type)
        services.start_processing(material, form.requests(), form.chosen_adjustments())
        return redirect('material_detail', pk=material.pk)


class MaterialDetailView(LoginRequiredMixin, TemplateView):
    """Shows the processing status and, once processed, the original vs. adapted comparison."""
    template_name = 'adaptations/material_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        material = own_material(self.request, kwargs['pk'])
        adaptations = list(material.adaptations.filter(record_status=RecordStatus.ACTIVE).select_related('student').order_by('student__created_at', 'version'))
        selected = None
        if adaptations:
            wanted = self.request.GET.get('a')
            selected = next((a for a in adaptations if str(a.pk) == wanted), None)
            if selected is None:
                # By default, the newest version of the first student
                first_student = adaptations[0].student_id
                selected = [a for a in adaptations if a.student_id == first_student][-1]
        context.update({
            'material': material,
            'analysis': material.ai_analysis or {},
            'original_figures': [
                {'number': n, 'description': f.get('description', ''),
                 'src': reverse('material_figure', args=[material.pk, n]) if figure_image_path(material, n) else ''}
                for n, f in enumerate((material.ai_analysis or {}).get('figures', []), start=1)
            ],
            'steps': steps_with_images(selected) if selected else [],
            'concepts': concepts_with_images(selected) if selected else [],
            'adaptations': adaptations,
            'selected': selected,
            'curriculum_warnings': curriculum_warnings(material, {a.student for a in adaptations}),
            'color_filters': ColorFilter.choices,
            'is_working': material.status in (MaterialStatus.PENDING, MaterialStatus.PROCESSING),
            'material_mismatch': material.status == MaterialStatus.ERROR and material.ai_error.startswith(ai.MATERIAL_MISMATCH),
            # Retrying a file blocked for privacy would send nothing new: the teacher uploads another version
            'can_resume': (material.status == MaterialStatus.ERROR and bool(material.job)
                           and not material.ai_error.startswith(ai.PRIVACY_BLOCK)),
            'privacy_blocked': material.status == MaterialStatus.ERROR and material.ai_error.startswith(ai.PRIVACY_BLOCK),
            # Our own PDFs carry this footer: an adapted worksheet has no original drawings to reuse
            'is_already_adapted': 'Adaptación DUA revisada por el docente' in (material.ai_analysis or {}).get('extracted_text', ''),
        })
        return context


def bank_cards(teacher):
    """The bank's cards: the newest version of every worksheet, with its outcome and the class it came from."""
    adaptations = (CurricularAdaptation.objects
                   .filter(material__teacher=teacher, record_status=RecordStatus.ACTIVE,
                           material__record_status=RecordStatus.ACTIVE, student__record_status=RecordStatus.ACTIVE)
                   .select_related('material__subject', 'student', 'evaluation')
                   .prefetch_related('material__lessons')
                   .order_by('-updated_at'))
    # One card per worksheet and student: only its newest version
    newest = {}
    for adaptation in adaptations:
        key = (adaptation.material_id, adaptation.student_id, adaptation.condition)
        if key not in newest or adaptation.version > newest[key].version:
            newest[key] = adaptation
    cards = sorted(newest.values(), key=lambda a: a.updated_at, reverse=True)
    for card in cards:
        evaluation = evaluation_of(card)
        card.outcome = evaluation.outcome
        card.has_draft = evaluation.status == EvaluationStatus.DRAFT and not evaluation._state.adding
        # Made in "Adaptar clase": link back to the class, its guide and its Excel
        card.lesson = next((lesson for lesson in card.material.lessons.all()
                            if lesson.record_status == RecordStatus.ACTIVE), None)
    return cards


def bank_filters(params):
    return {'student': params.get('alumno', ''), 'subject': params.get('materia', ''),
            'outcome': params.get('resultado', ''), 'query': params.get('q', '').strip()}


def filter_cards(cards, filters):
    """The cards that match the bank's filters (student, subject, classroom outcome, text)."""
    query = filters['query'].lower()
    return [
        card for card in cards
        if (not filters['student'] or str(card.student_id) == filters['student'])
        and (not filters['subject'] or card.material.subject.code == filters['subject'])
        and (not filters['outcome'] or card.outcome == filters['outcome'])
        and (not query or query in f'{card.title} {card.material.topic} {card.code}'.lower())
    ]


def filter_lessons(lessons, filters):
    """The bank's classes that match its filters (student, subject, text)."""
    query = filters['query'].lower()
    return [
        lesson for lesson in lessons
        if (not filters['student'] or any(str(a.student_id) == filters['student'] for a in lesson.active_adaptations))
        and (not filters['subject'] or (lesson.subject and lesson.subject.code == filters['subject']))
        and (not query or query in f'{lesson.session_label} {lesson.title}'.lower())
    ]


class BankView(LoginRequiredMixin, TemplateView):
    """
    Banco de adaptaciones, in two tabs: the adapted classes (each with its students' class and worksheet)
    and the loose worksheets made in "Adaptar material", with their classroom outcome and filters.
    """
    template_name = 'adaptations/bank.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        teacher = self.request.user
        all_cards = bank_cards(teacher)
        cards = [card for card in all_cards if card.lesson is None]  # worksheets of a class live in the class
        lessons = [lesson for lesson in teacher_lessons(teacher) if lesson.state in ('review', 'ready')]
        tab = self.request.GET.get('vista') or ('clases' if lessons or not cards else 'materiales')

        students = list(teacher.students.filter(record_status=RecordStatus.ACTIVE).order_by('created_at'))
        for student in students:
            student.card_count = (sum(1 for lesson in lessons if any(a.student_id == student.pk
                                                                     for a in lesson.active_adaptations))
                                  if tab == 'clases' else sum(1 for card in cards if card.student_id == student.pk))
        subjects = sorted({card.material.subject for card in cards} | {lesson.subject for lesson in lessons
                                                                       if lesson.subject},
                          key=lambda subject: subject.name)

        filters = bank_filters(self.request.GET)
        selected_student, selected_subject = filters['student'], filters['subject']
        selected_outcome, query = filters['outcome'], filters['query']
        shown = filter_cards(cards, filters)
        shown_lessons = filter_lessons(lessons, filters)
        for lesson in shown_lessons:
            for adaptation in lesson.active_adaptations:
                worksheet = adaptation.active_worksheet
                adaptation.evaluation_outcome = evaluation_of(worksheet).outcome if worksheet else None
        # The latest AI advice for the chosen student (or for any student)
        advised = [card.evaluation for card in all_cards
                   if (not selected_student or str(card.student_id) == selected_student)
                   and hasattr(card, 'evaluation') and card.evaluation.ai_recommendation]
        advice = max(advised, key=lambda evaluation: evaluation.recommended_at, default=None)
        context.update({
            'cards': shown, 'total': len(cards), 'students': students, 'subjects': subjects,
            'tab': tab, 'lessons': shown_lessons, 'lesson_total': len(lessons),
            'zip_query': self.request.GET.urlencode(),
            'outcomes': Outcome.choices, 'advice': advice, 'query': query,
            'selected_student': selected_student, 'selected_subject': selected_subject,
            'selected_outcome': selected_outcome,
        })
        return context


class BankZipView(LoginRequiredMixin, View):
    """"Descargar todo" in the bank's loose materials: the worksheets shown with its filters, a folder per student."""

    def get(self, request):
        loose = [card for card in bank_cards(request.user) if card.lesson is None]
        shown = filter_cards(loose, bank_filters(request.GET))
        if not shown:
            messages.error(request, 'No hay adaptaciones para descargar con estos filtros.')
            return redirect('bank')
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for card in shown:
                add_worksheet(archive, file_name(card.student.name), card)
        logger.info('Bank downloaded as ZIP (%s worksheets) by teacher=%s', len(shown), request.user.pk)
        return zip_response(buffer.getvalue(), f'banco-de-adaptaciones-{timezone.localdate():%Y-%m-%d}.zip')


def evaluation_of(adaptation):
    """The saved evaluation, or an empty one (pending) if the teacher has not evaluated yet."""
    try:
        return adaptation.evaluation
    except Evaluation.DoesNotExist:
        return Evaluation(adaptation=adaptation)


class MaterialStatusView(LoginRequiredMixin, View):
    """Polled by the page while the AI works."""

    def get(self, request, pk):
        material = own_material(request, pk)
        return JsonResponse({'status': material.status, 'label': material.get_status_display(), 'error': material.ai_error,
                             'progress': material.progress})


class MaterialResumeView(LoginRequiredMixin, View):
    """'Reintentar' after the AI failed (Google overloaded): keeps what was done and continues."""

    def post(self, request, pk):
        material = own_material(request, pk)
        if material.status == MaterialStatus.ERROR and services.resume_material(material):
            logger.info('Material %s resumed by teacher=%s', material.pk, request.user.pk)
        return redirect('material_detail', pk=material.pk)


class LessonResumeView(LoginRequiredMixin, View):
    """'Reintentar' a class that stopped: the pages, the plan and the students already done are kept."""

    def post(self, request, pk):
        lesson = own_lesson(request, pk)
        if lesson.status == MaterialStatus.ERROR and services.resume_lesson(lesson):
            logger.info('Lesson %s resumed by teacher=%s', lesson.pk, request.user.pk)
            return redirect('lesson_detail', pk=lesson.pk)
        return redirect('lesson_setup', pk=lesson.pk)


class MaterialRetryView(LoginRequiredMixin, View):
    """After a "does not match the subject or topic" stop, the teacher says the worksheet is right: adapt it."""

    def post(self, request, pk):
        material = own_material(request, pk)
        if material.status != MaterialStatus.ERROR or not material.ai_error.startswith(ai.MATERIAL_MISMATCH):
            return redirect('material_detail', pk=material.pk)
        if not services.retry_without_topic_check(material):
            messages.error(request, 'No se pudo repetir el pedido. Sube el material de nuevo.')
            return redirect('adapt')
        logger.info('Material %s adapted despite the mismatch warning, by teacher=%s', material.pk, request.user.pk)
        return redirect('material_detail', pk=material.pk)


class MaterialFileView(LoginRequiredMixin, View):
    """Serves the original worksheet only to its owner (uploaded files are never public)."""

    def get(self, request, pk):
        material = own_material(request, pk)
        if not material.file:
            return HttpResponse(status=404)
        response = FileResponse(material.file.open('rb'), filename=material.file_name or Path(material.file.name).name)
        response['Cache-Control'] = 'private, no-store'
        return response


class MaterialFigureView(LoginRequiredMixin, View):
    """Serves one cropped figure of the original worksheet, only to its owner."""

    def get(self, request, pk, number):
        material = own_material(request, pk)
        path = figure_image_path(material, number)
        if not path:
            raise Http404
        response = FileResponse(default_storage.open(path, 'rb'), content_type='image/png')
        response['Cache-Control'] = 'private, max-age=3600'
        return response


class AdaptationActionView(LoginRequiredMixin, View):
    """Approve, change the color filter or regenerate a simpler version."""

    def post(self, request, pk):
        adaptation = own_adaptation(request, pk)
        action = request.POST.get('action')
        target = adaptation
        if action == 'approve':
            adaptation.status = AdaptationStatus.APPROVED
            adaptation.save(update_fields=['status', 'updated_at'])
            messages.success(request, 'Ficha aprobada.' if request.POST.get('next')
                             else 'Adaptación aprobada. Ya puedes descargar el PDF.')
        elif action == 'color_filter' and request.POST.get('color_filter') in ColorFilter.values:
            adaptation.color_filter = request.POST['color_filter']
            adaptation.save(update_fields=['color_filter', 'updated_at'])
            if request.headers.get('x-requested-with') == 'fetch':
                return JsonResponse({'ok': True})
        elif action == 'regenerate':
            try:
                # No free text from the request reaches the AI (it used to carry the classroom observation)
                target = services.regenerate(adaptation, simplify=True)
                messages.success(request, f'Se generó la versión {target.version}, más simplificada.')
            except ai.AIError as error:
                messages.error(request, str(error))
        return redirect(back_to(request, f"{reverse('material_detail', args=[adaptation.material_id])}?a={target.pk}"))


class AdaptationDeleteView(LoginRequiredMixin, View):
    """Removes a worksheet (all its versions) from the bank: logical delete, the history stays in the DB."""

    def post(self, request, pk):
        adaptation = own_adaptation(request, pk)
        services.delete_worksheet(adaptation)
        logger.info('Adaptation %s deleted by teacher=%s', adaptation.code, request.user.pk)
        messages.success(request, f'Se eliminó la adaptación {adaptation.code}.')
        return redirect('bank')


class AdaptationEditView(LoginRequiredMixin, TemplateView):
    """'Ajustar manualmente': the teacher corrects the texts the AI wrote."""
    template_name = 'adaptations/adaptation_edit.html'
    TEXT_FIELDS = ['title', 'subtitle', 'intro', 'learning_goal', 'closing_message', 'active_break', 'finish_signal']
    # worked_example (intellectual disability) and concrete_support (dyscalculia) come from nee_modules
    STEP_FIELDS = ['title', 'instruction', 'hint', 'visual_support', 'figure_description', 'worked_example',
                   'concrete_support']
    CONCEPT_FIELDS = ['title', 'explanation', 'example']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['adaptation'] = own_adaptation(self.request, kwargs['pk'])
        context['content'] = context['adaptation'].content
        context['routine_labels'] = [('first', 'Primero'), ('then', 'Después'), ('last', 'Al final')]
        context['back_url'] = back_to(self.request, f"{reverse('material_detail', args=[context['adaptation'].material_id])}"
                                                    f"?a={context['adaptation'].pk}")
        return context

    def post(self, request, pk):
        adaptation = own_adaptation(request, pk)
        content = dict(adaptation.content)
        for field in self.TEXT_FIELDS:
            if field in request.POST:
                content[field] = request.POST[field].strip()
        steps = []
        for index, step in enumerate(content.get('steps', [])):
            updated = dict(step)
            for field in self.STEP_FIELDS:
                key = f'step_{index}_{field}'
                if key in request.POST:
                    updated[field] = request.POST[key].strip()
            if not request.POST.get(f'step_{index}_delete'):
                steps.append(updated)
        content['steps'] = steps
        concepts = []
        for index, concept in enumerate(content.get('concepts', [])):
            updated = dict(concept)
            for field in self.CONCEPT_FIELDS:
                key = f'concept_{index}_{field}'
                if key in request.POST:
                    updated[field] = request.POST[key].strip()
            if not request.POST.get(f'concept_{index}_delete'):
                concepts.append(updated)
        content['concepts'] = concepts
        if content.get('routine'):
            content['routine'] = {key: request.POST.get(f'routine_{key}', content['routine'].get(key, '')).strip()
                                  for key in ('first', 'then', 'last')}
        content['total_minutes'] = nee_modules.total_minutes(content)
        content['edited_by_teacher'] = timezone.now().isoformat()
        adaptation.content = content
        adaptation.title = (content.get('title') or adaptation.title)[:150]
        adaptation.status = AdaptationStatus.IN_REVIEW
        adaptation.save()
        logger.info('Adaptation %s edited by teacher=%s', adaptation.code, request.user.pk)
        messages.success(request, 'Cambios guardados. Revisa la guía y apruébala para descargarla.')
        return redirect(back_to(request, f"{reverse('material_detail', args=[adaptation.material_id])}?a={adaptation.pk}"))


def register_pdf_fonts():
    """
    Registers Atkinson Hyperlegible (high-legibility font) for the printable worksheets.
    Done directly in ReportLab: xhtml2pdf's @font-face copies fonts to temporary files
    that Windows keeps locked.
    """
    if 'Atkinson' in pdfmetrics.getRegisteredFontNames():
        return
    pdfmetrics.registerFont(TTFont('Atkinson', finders.find('fonts/AtkinsonHyperlegible-Regular.ttf')))
    pdfmetrics.registerFont(TTFont('Atkinson-Bold', finders.find('fonts/AtkinsonHyperlegible-Bold.ttf')))
    addMapping('Atkinson', 0, 0, 'Atkinson')
    addMapping('Atkinson', 1, 0, 'Atkinson-Bold')
    addMapping('Atkinson', 0, 1, 'Atkinson')
    addMapping('Atkinson', 1, 1, 'Atkinson-Bold')
    pisa_default.DEFAULT_FONT['atkinson'] = 'Atkinson'


def worksheet_pdf(adaptation):
    """The printable worksheet as PDF bytes (None if it could not be built); marks it as exported."""
    html = render_to_string('adaptations/adaptation_pdf.html', {
        'adaptation': adaptation,
        'content': adaptation.content,
        'steps': steps_with_images(adaptation, embed=True),
        'concepts': concepts_with_images(adaptation, embed=True),
        'background': PDF_BACKGROUNDS[adaptation.color_filter],
        'high_contrast': UdlAdjustment.HIGH_CONTRAST in adaptation.applied_adjustments,
    })
    register_pdf_fonts()
    pdf = BytesIO()
    if pisa.CreatePDF(html, dest=pdf, encoding='utf-8').err:
        logger.error('PDF failed for adaptation %s', adaptation.code)
        return None
    if adaptation.status != AdaptationStatus.EXPORTED:
        adaptation.status = AdaptationStatus.EXPORTED
        adaptation.save(update_fields=['status', 'updated_at'])
    return pdf.getvalue()


FILE_NAME_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def file_name(text):
    """Text usable as a file or folder name on Windows, macOS and Linux."""
    return ' '.join(FILE_NAME_FORBIDDEN.sub(' ', text).split()).strip(' .')[:80] or 'archivo'


PENDING_NOTE = ('La ficha de {name} ({code}) todavía está en revisión. Ábrela en Maestro Sombra, revísala y '
                'apruébala; después vuelve a descargar todo para que aparezca aquí en PDF.\n')


def add_worksheet(archive, folder, adaptation):
    """The approved worksheet as PDF in the student's folder, or a note if it still needs approval."""
    name = adaptation.student.name
    if adaptation.status == AdaptationStatus.IN_REVIEW:
        archive.writestr(f'{folder}/Ficha pendiente de aprobar - {file_name(adaptation.title)}.txt',
                         PENDING_NOTE.format(name=name, code=adaptation.code))
        return
    pdf = worksheet_pdf(adaptation)
    if pdf:
        archive.writestr(f'{folder}/Ficha {adaptation.code} - {file_name(adaptation.title)}.pdf', pdf)


def zip_response(archive_bytes, name):
    response = HttpResponse(archive_bytes, content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{name}"'
    response['Cache-Control'] = 'private, no-store'
    return response


class AdaptationPdfView(LoginRequiredMixin, View):
    """Accessible printable worksheet. Only available after the teacher approves it."""

    def get(self, request, pk):
        adaptation = own_adaptation(request, pk)
        if adaptation.status == AdaptationStatus.IN_REVIEW:
            messages.error(request, 'Revisa y aprueba la adaptación antes de descargarla.')
            return redirect(f"{reverse('material_detail', args=[adaptation.material_id])}?a={adaptation.pk}")
        pdf = worksheet_pdf(adaptation)
        if pdf is None:
            messages.error(request, 'No se pudo generar el PDF. Inténtalo de nuevo.')
            return redirect('material_detail', pk=adaptation.material_id)
        logger.info('Adaptation %s exported to PDF by teacher=%s', adaptation.code, request.user.pk)
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{adaptation.code}-v{adaptation.version}.pdf"'
        return response


# ---------- "Adaptar clase": the whole class, from the teacher's plan ----------

def own_lesson(request, pk):
    return get_object_or_404(LessonPlan.objects.select_related('subject', 'material'), pk=pk,
                             teacher=request.user, record_status=RecordStatus.ACTIVE)


def teacher_lessons(teacher):
    """
    The teacher's classes, newest first, each with: the students it was adapted for (active_adaptations),
    how many are fully reviewed (ready_count), its state (state, state_label, is_finished) and the page
    where the teacher continues (continue_url).
    """
    lessons = list(LessonPlan.objects.filter(teacher=teacher, record_status=RecordStatus.ACTIVE)
                   .select_related('subject')
                   .prefetch_related('adaptations__student', 'adaptations__worksheet'))
    for lesson in lessons:
        lesson.active_adaptations = [a for a in lesson.adaptations.all() if a.record_status == RecordStatus.ACTIVE]
        lesson.ready_count = sum(1 for a in lesson.active_adaptations if a.is_ready)
        detail = reverse('lesson_detail', args=[lesson.pk])
        if not lesson.material_id and lesson.status == MaterialStatus.PENDING:
            lesson.state, lesson.state_label = 'setup', 'Falta configurar'
            lesson.continue_url = reverse('lesson_setup', args=[lesson.pk])
        elif lesson.status in (MaterialStatus.PENDING, MaterialStatus.PROCESSING):
            lesson.state, lesson.state_label, lesson.continue_url = 'working', 'La IA está trabajando', detail
        elif lesson.status == MaterialStatus.ERROR:
            lesson.state, lesson.state_label, lesson.continue_url = 'error', 'No se pudo adaptar', detail
        elif lesson.active_adaptations and lesson.ready_count == len(lesson.active_adaptations):
            lesson.state, lesson.state_label = 'ready', 'Lista'
            lesson.continue_url = f'{detail}?paso=listo'
        else:
            lesson.state = 'review'
            lesson.state_label = f'En revisión ({lesson.ready_count} de {len(lesson.active_adaptations)} listos)'
            lesson.continue_url = f'{detail}?siguiente=1'
        lesson.is_finished = lesson.state == 'ready'
    return lessons


class LessonUploadView(LoginRequiredMixin, FormView):
    """Step 1: the class plan in Excel. Above it, the classes not finished yet, to continue them."""
    template_name = 'adaptations/lesson_upload.html'
    form_class = LessonUploadForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['open_lessons'] = [lesson for lesson in teacher_lessons(self.request.user) if not lesson.is_finished]
        return context

    def form_valid(self, form):
        upload = form.cleaned_data['plan_file']
        name = Path(upload.name).name[:255]
        # Uploading the same plan again replaces the earlier uploads never configured (nothing was done
        # with them), so "Mis clases en curso" does not show the same file twice
        for earlier in LessonPlan.objects.filter(teacher=self.request.user, plan_file_name=name, material__isnull=True,
                                                 status=MaterialStatus.PENDING, record_status=RecordStatus.ACTIVE):
            services.delete_lesson(earlier)
        lesson = LessonPlan(teacher=self.request.user, plan_file_name=name)
        lesson.plan_file = upload
        lesson.save()
        logger.info('Lesson plan %s uploaded by teacher=%s', lesson.pk, self.request.user.pk)
        return redirect('lesson_setup', pk=lesson.pk)


class LessonSetupView(LoginRequiredMixin, FormView):
    """Step 2: which session, its book pages, the optional guide and the students."""
    template_name = 'adaptations/lesson_setup.html'
    form_class = LessonSetupForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.lesson = own_lesson(request, kwargs['pk'])
            if not self.lesson.plan_file:
                # Already adapted: its plan file was deleted, so a new class starts with a new upload
                messages.info(request, 'Esa clase ya fue adaptada. Sube tu planificación para adaptar otra.')
                return redirect('lesson_upload')
            with self.lesson.plan_file.open('rb') as plan:
                self.plan_content = plan.read()
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(teacher=self.request.user, sessions=lesson_plan.sessions(self.plan_content))
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context['form']
        context.update({
            'lesson': self.lesson,
            'topic_mismatch': self.lesson.ai_error.startswith(ai.TOPIC_MISMATCH),
            'student_options': [
                {'option': option, 'student': form.student_objects[option.data['value']]}
                for option in form['students']
            ],
        })
        return context

    def form_valid(self, form):
        data = form.cleaned_data
        session = form.chosen_session()
        lesson = self.lesson
        title = (session['title'] or session['label'])[:150]
        material = OriginalMaterial(
            teacher=self.request.user, subject=data['subject'], title=title,
            topic=f"{session['label']} · {session['title']}".strip(' ·')[:150], input_type=InputType.PDF,
        )
        material.file.save(f'{material.id}.pdf', ContentFile(figures.to_pdf(data['book_files'])), save=False)
        material.file_name = f'Libro del alumno · {title}'[:255]
        material.size_kb = max(1, round(material.file.size / 1024))
        material.full_clean()
        material.save()
        if lesson.material_id:
            services.discard_material(lesson.material)  # a retry: the pages of the failed attempt go
        if lesson.guide_file:
            lesson.guide_file.delete(save=False)
        if data['guide_files']:
            lesson.guide_file.save('guia.pdf', ContentFile(figures.to_pdf(data['guide_files'])), save=False)
        lesson.subject = data['subject']
        lesson.material = material
        lesson.sheet, lesson.session_row = session['sheet'], session['row']
        lesson.session_label, lesson.title = session['label'][:100], title
        lesson.session_text = lesson_plan.session_text(self.plan_content, session['sheet'], session['row'],
                                                       session.get('last_row'))
        lesson.status = MaterialStatus.PENDING
        lesson.save()
        logger.info('Lesson %s (%s) sent to the AI by teacher=%s', lesson.pk, lesson.session_label, self.request.user.pk)
        lesson.adaptations.all().delete()  # a new attempt from this form starts clean
        services.start_lesson_processing(lesson, form.requests(), form.chosen_adjustments(),
                                         data.get('adaptation_grade') or '', not data.get('skip_topic_check'))
        return redirect('lesson_detail', pk=lesson.pk)


def lesson_step_url(lesson_id, adaptation_id, step):
    return f"{reverse('lesson_detail', args=[lesson_id])}?a={adaptation_id}&paso={step}"


class LessonDetailView(LoginRequiredMixin, TemplateView):
    """
    The class review, one screen at a time for each student: step 1, the adapted class as it will be in
    the Excel (editable); step 2, the worksheet; then the next student. When all are approved, the
    final screen offers the whole class as a ZIP. ?siguiente=1 jumps to the first thing still pending.
    """
    template_name = 'adaptations/lesson_detail.html'

    def adaptations(self, lesson):
        return list(lesson.adaptations.filter(record_status=RecordStatus.ACTIVE)
                    .select_related('student', 'worksheet__material__subject', 'worksheet__student'))

    def get(self, request, *args, **kwargs):
        if request.GET.get('siguiente'):
            lesson = own_lesson(request, kwargs['pk'])
            adaptations = self.adaptations(lesson)
            pending = next((a for a in adaptations if not a.is_ready), None)
            if pending is None:
                return redirect(f"{reverse('lesson_detail', args=[lesson.pk])}?paso=listo")
            done = request.GET.get('de')
            if done and done != str(pending.pk):
                messages.info(request, f'Ahora revisa la clase y la ficha de {pending.student.name}.')
            return redirect(lesson_step_url(lesson.pk, pending.pk, 1 if not pending.is_class_approved else 2))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lesson = own_lesson(self.request, kwargs['pk'])
        adaptations = self.adaptations(lesson)
        all_ready = bool(adaptations) and all(a.is_ready for a in adaptations)
        wanted, step = self.request.GET.get('a'), self.request.GET.get('paso', '')
        selected = next((a for a in adaptations if str(a.pk) == wanted), None)
        if step == 'listo' and all_ready:
            selected = None
        else:
            selected = selected or next((a for a in adaptations if not a.is_ready), adaptations[0] if adaptations else None)
            if step not in ('1', '2'):
                step = '2' if selected and selected.is_class_approved else '1'
            if step == '2' and selected and not selected.is_class_approved:
                step = '1'  # the worksheet comes after the class
        worksheet = selected.active_worksheet if selected else None
        context.update({
            'lesson': lesson, 'analysis': lesson.analysis or {}, 'adaptations': adaptations, 'selected': selected,
            'step': step if selected else 'listo', 'all_ready': all_ready,
            'is_working': lesson.status in (MaterialStatus.PENDING, MaterialStatus.PROCESSING),
            # Google overloaded (not pages of another topic): the class can continue from where it stopped
            'can_resume': (lesson.status == MaterialStatus.ERROR and bool(lesson.plan_file)
                           and not lesson.ai_error.startswith(ai.TOPIC_MISMATCH)
                           and ai.PRIVACY_BLOCK not in lesson.ai_error
                           and bool(lesson.material and lesson.material.job)),
            'plan_row': plan_row_text(selected) if selected else '',
            'moments': class_moments(lesson, selected) if selected else [],
            'plan_row_text': '\n'.join(selected.content.get('plan_row', [])) if selected else '',
            'metacognition_text': '\n'.join(selected.content.get('metacognition', [])) if selected else '',
            'grade_choices': AdaptationGrade.choices,
            'worksheet': worksheet,
            'concepts': concepts_with_images(worksheet) if worksheet and step == '2' else [],
            'steps': steps_with_images(worksheet) if worksheet and step == '2' else [],
            # Worksheet actions come back to step 2; approving it moves on to whatever is pending
            'next_url': lesson_step_url(lesson.pk, selected.pk, 2) if selected else '',
            'approve_next_url': (f"{reverse('lesson_detail', args=[lesson.pk])}?siguiente=1&de={selected.pk}"
                                 if selected else ''),
        })
        return context


def class_moments(lesson, adaptation):
    """The adapted moments next to what the whole class does in each one (as in the Excel)."""
    originals = {lesson_plan.plain(m.get('name')): m.get('activities', '')
                 for m in (lesson.analysis or {}).get('moments', [])}
    return [dict(moment, class_activities=originals.get(lesson_plan.plain(moment.get('name')), ''),
                 teacher_actions_text='\n'.join(moment.get('teacher_actions', [])),
                 supports_text='\n'.join(moment.get('supports', [])))
            for moment in adaptation.content.get('moments', [])]


def lines(text):
    return [line.strip(' •-\t') for line in (text or '').splitlines() if line.strip(' •-\t')]


class LessonSaveView(LoginRequiredMixin, View):
    """Step 1: the teacher's corrections to the adapted class (what goes in the Excel), and its approval."""

    def post(self, request, pk):
        adaptation = get_object_or_404(
            LessonAdaptation, pk=pk, lesson__teacher=request.user, lesson__record_status=RecordStatus.ACTIVE,
            record_status=RecordStatus.ACTIVE,
        )
        data = request.POST
        content = dict(adaptation.content)
        for field in ('grade_reason', 'adapted_objective', 'evaluation'):
            if field in data:
                content[field] = data[field].strip()
        for field in ('plan_row', 'metacognition'):
            if field in data:
                content[field] = lines(data[field])
        moments = []
        for index, moment in enumerate(content.get('moments', [])):
            moment = dict(moment)
            for field in ('duration', 'student_task', 'watch_for'):
                if f'moment_{index}_{field}' in data:
                    moment[field] = data[f'moment_{index}_{field}'].strip()
            for field in ('teacher_actions', 'supports'):
                if f'moment_{index}_{field}' in data:
                    moment[field] = lines(data[f'moment_{index}_{field}'])
            moments.append(moment)
        content['moments'] = moments
        if not content.get('plan_row'):
            messages.error(request, 'La fila de adaptación curricular no puede quedar vacía.')
            return redirect(lesson_step_url(adaptation.lesson_id, adaptation.pk, 1))
        adaptation.content = content
        if data.get('grade') in AdaptationGrade.values:
            adaptation.grade = data['grade']
        approve = data.get('action') == 'approve'
        adaptation.status = AdaptationStatus.APPROVED if approve else AdaptationStatus.IN_REVIEW
        adaptation.save(update_fields=['content', 'grade', 'status', 'updated_at'])
        logger.info('Lesson adaptation %s saved (approved=%s) by teacher=%s', adaptation.pk, approve, request.user.pk)
        if approve:
            messages.success(request, f'Clase de {adaptation.student.name} aprobada. Ahora revisa su ficha.')
            return redirect(lesson_step_url(adaptation.lesson_id, adaptation.pk, 2))
        messages.success(request, 'Cambios guardados. Cuando esté lista, apruébala.')
        return redirect(lesson_step_url(adaptation.lesson_id, adaptation.pk, 1))


class LessonDeleteView(LoginRequiredMixin, View):
    """Removes an adapted class with its worksheets and stored files: logical delete."""

    def post(self, request, pk):
        lesson = own_lesson(request, pk)
        services.delete_lesson(lesson)
        logger.info('Lesson %s deleted by teacher=%s', lesson.pk, request.user.pk)
        messages.success(request, 'Se eliminó la clase adaptada y sus fichas.')
        # Back to the screen it was deleted from ("Mis clases en curso", the bank); from the class itself,
        # whose page no longer exists, to "Adaptar clase"
        return redirect(back_to(request, reverse('lesson_upload')))


class LessonStatusView(LoginRequiredMixin, View):
    def get(self, request, pk):
        lesson = own_lesson(request, pk)
        return JsonResponse({'status': lesson.status, 'label': lesson.get_status_display(), 'error': lesson.ai_error,
                             'progress': lesson.progress})


def plan_row_text(adaptation):
    """The "Adaptación curricular" text. The student appears by pseudonym, added here: the AI never sees it."""
    grade = GRADE_NUMBERS[adaptation.grade]
    actions = ' / '.join(adaptation.content.get('plan_row', []))
    return f'Para el estudiante {adaptation.student.pseudonym} (Grado {grade}) {actions}'


def class_sheet(adaptation):
    """(student name, data) of one student's adapted class, for lesson_plan.adapted_class."""
    lesson = adaptation.lesson
    return adaptation.student.name, {
        'analysis': lesson.analysis or {}, 'content': adaptation.content, 'subject': lesson.subject.name,
        'grade': adaptation.student.get_grade_display(), 'pseudonym': adaptation.student.pseudonym,
        'condition': adaptation.conditions_label, 'adaptation_grade': adaptation.get_grade_display(),
        'plan_row': plan_row_text(adaptation),
    }


class LessonExcelView(LoginRequiredMixin, View):
    """
    Only the chosen session, adapted for the student, as a new file without personal or school data, in the
    format the teacher uploaded the plan in: Excel, PDF or Word.
    """

    def get(self, request, pk):
        adaptation = get_object_or_404(
            LessonAdaptation.objects.select_related('lesson__subject', 'student'), pk=pk,
            lesson__teacher=request.user, lesson__record_status=RecordStatus.ACTIVE,
            record_status=RecordStatus.ACTIVE,
        )
        lesson = adaptation.lesson
        content, extension, content_type = lesson_plan.adapted_class([class_sheet(adaptation)], lesson.plan_format)
        logger.info('Lesson %s exported (%s) by teacher=%s', lesson.pk, extension, request.user.pk)
        response = HttpResponse(content, content_type=content_type)
        name = f'clase-adaptada-{slugify(lesson.session_label)}-{adaptation.student.pseudonym.lower()}{extension}'
        response['Content-Disposition'] = f'attachment; filename="{name}"'
        response['Cache-Control'] = 'private, no-store'
        return response


class LessonZipView(LoginRequiredMixin, View):
    """
    "Descargar todo": one folder per student with their adapted class (in the plan's format: Excel, PDF or
    Word) and their worksheet (PDF).
    Only once every class and worksheet is approved. Built in memory for the class's teacher; nothing is stored.
    """

    def get(self, request, pk):
        lesson = own_lesson(request, pk)
        adaptations = list(lesson.adaptations.filter(record_status=RecordStatus.ACTIVE)
                           .select_related('lesson__subject', 'student', 'worksheet__material__subject',
                                           'worksheet__student'))
        if not adaptations:
            messages.error(request, 'Esta clase todavía no tiene adaptaciones para descargar.')
            return redirect('lesson_detail', pk=lesson.pk)
        pending = [a.student.name for a in adaptations if not a.is_ready]
        if pending:
            messages.error(request, f'Falta aprobar la clase o la ficha de: {", ".join(pending)}.')
            return redirect('lesson_detail', pk=lesson.pk)
        session = file_name(lesson.session_label or 'Clase')
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for adaptation in adaptations:
                name = file_name(adaptation.student.name)
                content, extension, _type = lesson_plan.adapted_class([class_sheet(adaptation)], lesson.plan_format)
                archive.writestr(f'{name}/Clase adaptada - {name}{extension}', content)
                worksheet = adaptation.active_worksheet
                if worksheet:
                    pdf = worksheet_pdf(worksheet)
                    if pdf:
                        archive.writestr(f'{name}/Ficha - {name}.pdf', pdf)
        logger.info('Lesson %s downloaded as ZIP by teacher=%s', lesson.pk, request.user.pk)
        return zip_response(buffer.getvalue(), f'clase-adaptada-{slugify(session)}.zip')

