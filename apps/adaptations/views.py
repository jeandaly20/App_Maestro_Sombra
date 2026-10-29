import base64
import logging
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
from django.views import View
from django.views.generic import FormView, TemplateView
from PIL import Image
from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from xhtml2pdf import default as pisa_default
from xhtml2pdf import pisa

from apps.core.choices import GRADE_NUMBERS, ColorFilter, RecordStatus, UdlAdjustment
from apps.evaluations.models import Evaluation, EvaluationStatus, Outcome
from apps.materials.models import InputType, MaterialStatus, OriginalMaterial

from . import ai, figures, lesson_plan, services, visuals
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
                {'option': option, 'student': form.student_objects[option.data['value']],
                 'condition_field': form.condition_fields().get(option.data['value'])}
                for option in form['students']
            ],
            'recommended_by_student': {
                pk: [str(a) for group in s.groups for a in group.adjustments]
                for pk, s in form.student_objects.items()
            },
            'adjustment_descriptions': dict(UdlAdjustment.choices),
        })
        return context

    def form_valid(self, form):
        data = form.cleaned_data
        file = data.get('file') if data['input_type'] != 'TEXT' else None
        material = OriginalMaterial(
            teacher=self.request.user, subject=data['subject'], title=data['title'], topic=data['topic'],
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
            # Our own PDFs carry this footer: an adapted worksheet has no original drawings to reuse
            'is_already_adapted': 'Adaptación DUA revisada por el docente' in (material.ai_analysis or {}).get('extracted_text', ''),
        })
        return context


class BankView(LoginRequiredMixin, TemplateView):
    """Banco de adaptaciones: the newest version of every worksheet, its classroom outcome and filters."""
    template_name = 'adaptations/bank.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        teacher = self.request.user
        adaptations = (CurricularAdaptation.objects
                       .filter(material__teacher=teacher, record_status=RecordStatus.ACTIVE,
                               material__record_status=RecordStatus.ACTIVE, student__record_status=RecordStatus.ACTIVE)
                       .select_related('material__subject', 'student', 'evaluation')
                       .order_by('-updated_at'))
        # One card per worksheet, student and condition: only its newest version
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

        students = list(teacher.students.filter(record_status=RecordStatus.ACTIVE).order_by('created_at'))
        for student in students:
            student.card_count = sum(1 for card in cards if card.student_id == student.pk)
        subjects = sorted({card.material.subject for card in cards}, key=lambda subject: subject.name)

        selected_student = self.request.GET.get('alumno', '')
        selected_subject = self.request.GET.get('materia', '')
        selected_outcome = self.request.GET.get('resultado', '')
        query = self.request.GET.get('q', '').strip()
        shown = [
            card for card in cards
            if (not selected_student or str(card.student_id) == selected_student)
            and (not selected_subject or card.material.subject.code == selected_subject)
            and (not selected_outcome or card.outcome == selected_outcome)
            and (not query or query.lower() in f'{card.title} {card.material.topic} {card.code}'.lower())
        ]
        # The latest AI advice for the chosen student (or for any student)
        advised = [card.evaluation for card in cards
                   if (not selected_student or str(card.student_id) == selected_student)
                   and hasattr(card, 'evaluation') and card.evaluation.ai_recommendation]
        advice = max(advised, key=lambda evaluation: evaluation.recommended_at, default=None)
        context.update({
            'cards': shown, 'total': len(cards), 'students': students, 'subjects': subjects,
            'outcomes': Outcome.choices, 'advice': advice, 'query': query,
            'selected_student': selected_student, 'selected_subject': selected_subject,
            'selected_outcome': selected_outcome,
        })
        return context


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
        return JsonResponse({'status': material.status, 'label': material.get_status_display(), 'error': material.ai_error})


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
            messages.success(request, 'Adaptación aprobada. Ya puedes descargar el PDF.')
        elif action == 'color_filter' and request.POST.get('color_filter') in ColorFilter.values:
            adaptation.color_filter = request.POST['color_filter']
            adaptation.save(update_fields=['color_filter', 'updated_at'])
            if request.headers.get('x-requested-with') == 'fetch':
                return JsonResponse({'ok': True})
        elif action == 'regenerate':
            try:
                target = services.regenerate(adaptation, simplify=True, feedback=request.POST.get('feedback', ''))
                messages.success(request, f'Se generó la versión {target.version}, más simplificada.')
            except ai.AIError as error:
                messages.error(request, str(error))
        return redirect(f"{reverse('material_detail', args=[adaptation.material_id])}?a={target.pk}")


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
    TEXT_FIELDS = ['title', 'subtitle', 'intro', 'learning_goal', 'closing_message']
    STEP_FIELDS = ['title', 'instruction', 'hint', 'visual_support', 'figure_description']
    CONCEPT_FIELDS = ['title', 'explanation', 'example']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['adaptation'] = own_adaptation(self.request, kwargs['pk'])
        context['content'] = context['adaptation'].content
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
        content['edited_by_teacher'] = timezone.now().isoformat()
        adaptation.content = content
        adaptation.title = (content.get('title') or adaptation.title)[:150]
        adaptation.status = AdaptationStatus.IN_REVIEW
        adaptation.save()
        logger.info('Adaptation %s edited by teacher=%s', adaptation.code, request.user.pk)
        messages.success(request, 'Cambios guardados. Revisa la guía y apruébala para descargarla.')
        return redirect(f"{reverse('material_detail', args=[adaptation.material_id])}?a={adaptation.pk}")


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


class AdaptationPdfView(LoginRequiredMixin, View):
    """Accessible printable worksheet. Only available after the teacher approves it."""

    def get(self, request, pk):
        adaptation = own_adaptation(request, pk)
        if adaptation.status == AdaptationStatus.IN_REVIEW:
            messages.error(request, 'Revisa y aprueba la adaptación antes de descargarla.')
            return redirect(f"{reverse('material_detail', args=[adaptation.material_id])}?a={adaptation.pk}")
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
            messages.error(request, 'No se pudo generar el PDF. Inténtalo de nuevo.')
            return redirect('material_detail', pk=adaptation.material_id)
        if adaptation.status != AdaptationStatus.EXPORTED:
            adaptation.status = AdaptationStatus.EXPORTED
            adaptation.save(update_fields=['status', 'updated_at'])
        logger.info('Adaptation %s exported to PDF by teacher=%s', adaptation.code, request.user.pk)
        response = HttpResponse(pdf.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{adaptation.code}-v{adaptation.version}.pdf"'
        return response


# ---------- "Adaptar clase": the whole class, from the teacher's plan ----------

def own_lesson(request, pk):
    return get_object_or_404(LessonPlan.objects.select_related('subject', 'material'), pk=pk,
                             teacher=request.user, record_status=RecordStatus.ACTIVE)


class LessonUploadView(LoginRequiredMixin, FormView):
    """Step 1: the class plan in Excel."""
    template_name = 'adaptations/lesson_upload.html'
    form_class = LessonUploadForm

    def form_valid(self, form):
        upload = form.cleaned_data['plan_file']
        lesson = LessonPlan(teacher=self.request.user, plan_file_name=Path(upload.name).name[:255])
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
            'student_options': [
                {'option': option, 'student': form.student_objects[option.data['value']],
                 'condition_field': form.condition_fields().get(option.data['value'])}
                for option in form['students']
            ],
            'recommended_by_student': {
                pk: [str(a) for group in s.groups for a in group.adjustments]
                for pk, s in form.student_objects.items()
            },
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
        if data['guide_files']:
            lesson.guide_file.save('guia.pdf', ContentFile(figures.to_pdf(data['guide_files'])), save=False)
        lesson.subject = data['subject']
        lesson.material = material
        lesson.sheet, lesson.session_row = session['sheet'], session['row']
        lesson.session_label, lesson.title = session['label'][:100], title
        lesson.session_text = lesson_plan.session_text(self.plan_content, session['sheet'], session['row'])
        lesson.status = MaterialStatus.PENDING
        lesson.save()
        logger.info('Lesson %s (%s) sent to the AI by teacher=%s', lesson.pk, lesson.session_label, self.request.user.pk)
        services.start_lesson_processing(lesson, form.requests(), form.chosen_adjustments(),
                                         data.get('adaptation_grade') or '')
        return redirect('lesson_detail', pk=lesson.pk)


class LessonDetailView(LoginRequiredMixin, TemplateView):
    """The adapted class for each student: plan row, shadow-teacher guide, worksheet and Excel."""
    template_name = 'adaptations/lesson_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lesson = own_lesson(self.request, kwargs['pk'])
        adaptations = list(lesson.adaptations.filter(record_status=RecordStatus.ACTIVE)
                           .select_related('student', 'worksheet'))
        wanted = self.request.GET.get('a')
        selected = next((a for a in adaptations if str(a.pk) == wanted), adaptations[0] if adaptations else None)
        context.update({
            'lesson': lesson, 'analysis': lesson.analysis or {}, 'adaptations': adaptations, 'selected': selected,
            'is_working': lesson.status in (MaterialStatus.PENDING, MaterialStatus.PROCESSING),
            'plan_row': plan_row_text(selected) if selected else '',
        })
        return context


class LessonDeleteView(LoginRequiredMixin, View):
    """Removes an adapted class with its worksheets and stored files: logical delete."""

    def post(self, request, pk):
        lesson = own_lesson(request, pk)
        services.delete_lesson(lesson)
        logger.info('Lesson %s deleted by teacher=%s', lesson.pk, request.user.pk)
        messages.success(request, 'Se eliminó la clase adaptada y sus fichas.')
        return redirect('bank')


class LessonStatusView(LoginRequiredMixin, View):
    def get(self, request, pk):
        lesson = own_lesson(request, pk)
        return JsonResponse({'status': lesson.status, 'label': lesson.get_status_display(), 'error': lesson.ai_error})


def plan_row_text(adaptation):
    """The "Adaptación curricular" text. The student appears by pseudonym, added here: the AI never sees it."""
    grade = GRADE_NUMBERS[adaptation.grade]
    actions = ' / '.join(adaptation.content.get('plan_row', []))
    return f'Para el estudiante {adaptation.student.pseudonym} (Grado {grade}) {actions}'


class LessonExcelView(LoginRequiredMixin, View):
    """Only the chosen session, adapted for the student, as a new Excel without personal or school data."""

    def get(self, request, pk):
        adaptation = get_object_or_404(
            LessonAdaptation.objects.select_related('lesson__subject', 'student'), pk=pk,
            lesson__teacher=request.user, lesson__record_status=RecordStatus.ACTIVE,
        )
        lesson = adaptation.lesson
        content = lesson_plan.adapted_workbook(
            analysis=lesson.analysis or {}, content=adaptation.content, subject=lesson.subject.name,
            grade=adaptation.student.get_grade_display(), pseudonym=adaptation.student.pseudonym,
            condition=adaptation.get_condition_display(), adaptation_grade=adaptation.get_grade_display(),
            plan_row=plan_row_text(adaptation),
        )
        logger.info('Lesson %s exported to Excel by teacher=%s', lesson.pk, request.user.pk)
        response = HttpResponse(
            content, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        name = f'clase-adaptada-{lesson.session_label}-{adaptation.student.pseudonym}.xlsx'.replace(' ', '-').lower()
        response['Content-Disposition'] = f'attachment; filename="{name}"'
        response['Cache-Control'] = 'private, no-store'
        return response
