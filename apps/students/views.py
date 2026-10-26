import logging
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, InternalError, transaction
from django.core.files.storage import default_storage
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.template.loader import render_to_string
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, UpdateView
from xhtml2pdf import pisa

from apps.core.choices import CONDITION_DESCRIPTIONS, CONDITION_GROUPS, CONDITION_SHORT_NAMES, RecordStatus

from . import documents
from .forms import StudentForm
from .models import MAX_STUDENTS_PER_TEACHER, Student

logger = logging.getLogger('maestro_sombra')


def students_of(teacher):
    return (Student.objects.filter(teacher=teacher, record_status=RecordStatus.ACTIVE)
            .annotate(adaptation_count=Count('adaptations'))
            .order_by('created_at'))


class StudentPageMixin(LoginRequiredMixin):
    """Shared by the register and edit views: the form on the left, the directory on the right."""
    model = Student
    form_class = StudentForm
    template_name = 'students/student_page.html'
    success_url = reverse_lazy('student_list')

    def get_queryset(self):
        # A teacher can only open their own students; anyone else gets a 404
        return Student.objects.filter(teacher=self.request.user, record_status=RecordStatus.ACTIVE)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context['form']
        checkboxes = {option.data['value']: option for option in form['conditions']}
        students = students_of(self.request.user)
        context.update({
            'students': students,
            'max_students': MAX_STUDENTS_PER_TEACHER,
            'is_full': students.count() >= MAX_STUDENTS_PER_TEACHER,
            'condition_groups': [
                {
                    'group': group,
                    'options': [(checkboxes[c.value], CONDITION_SHORT_NAMES[c], CONDITION_DESCRIPTIONS[c])
                                for c in group.conditions],
                }
                for group in CONDITION_GROUPS
            ],
        })
        return context

    def form_valid(self, form):
        try:
            with transaction.atomic():
                # Set before saving, so the database rules (date or document, consent) see the final state
                replaced = ''
                if form.cleaned_data.get('diagnosis_document'):
                    replaced = documents.save_document(form.instance, form.cleaned_data['diagnosis_document'],
                                                       form.document_type)
                elif form.cleaned_data.get('remove_document'):
                    documents.delete_document(form.instance, save=False)
                elif form.instance.has_document:
                    documents.recheck_document(form.instance)
                response = super().form_valid(form)
            if replaced:
                default_storage.delete(replaced)
            return response
        except (IntegrityError, InternalError):
            # The PostgreSQL trigger caught a 4th student (e.g. two tabs saving at once)
            form.add_error(None, f'Un docente puede registrar como máximo {MAX_STUDENTS_PER_TEACHER} estudiantes.')
            return self.form_invalid(form)


class StudentCreateView(StudentPageMixin, CreateView):

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # The teacher is set before validating, so the model can check the 3-student limit
        kwargs['instance'] = Student(teacher=self.request.user)
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.object and self.object.pk and not form.errors:
            logger.info('Student registered: %s by teacher=%s', self.object.pseudonym, self.request.user.pk)
            messages.success(self.request, f'Ficha de {self.object.name} guardada ({self.object.pseudonym}).')
        return response


class StudentUpdateView(StudentPageMixin, UpdateView):

    def form_valid(self, form):
        response = super().form_valid(form)
        if not form.errors:
            logger.info('Student updated: %s by teacher=%s', self.object.pseudonym, self.request.user.pk)
            messages.success(self.request, f'Ficha de {self.object.name} actualizada.')
        return response


class StudentDeactivateView(LoginRequiredMixin, View):
    """Gives a student leave: logical delete, so the history (adaptations, evaluations) is kept."""

    def post(self, request, pk):
        student = get_object_or_404(Student, pk=pk, teacher=request.user, record_status=RecordStatus.ACTIVE)
        documents.delete_document(student, save=False)  # leave: the history stays, the health document goes
        student.record_status = RecordStatus.INACTIVE
        student.save(update_fields=['record_status', 'diagnosis_file', 'diagnosis_file_type', 'diagnosis_consent_at',
                                    'diagnosis_check', 'updated_at'])
        logger.info('Student deactivated: %s by teacher=%s', student.pseudonym, request.user.pk)
        messages.success(request, f'{student.name} fue dado de baja. Su historial se conserva y se liberó un cupo.')
        return redirect('student_list')


class StudentDocumentView(LoginRequiredMixin, View):
    """Opens the diagnosis document, decrypted, only for the student's teacher. Every opening is logged."""

    def get(self, request, pk):
        student = get_object_or_404(Student, pk=pk, teacher=request.user, record_status=RecordStatus.ACTIVE)
        if not student.has_document:
            raise Http404
        logger.info('Diagnosis document opened: %s by teacher=%s', student.pseudonym, request.user.pk)
        response = HttpResponse(documents.read_document(student), content_type=student.diagnosis_file_type)
        response['Content-Disposition'] = f'inline; filename="{documents.download_name(student)}"'
        response['Cache-Control'] = 'private, no-store'
        return response


class StudentRecordsPdfView(LoginRequiredMixin, View):
    """Downloads the teacher's student records as a PDF (personal data: teacher only)."""

    def get(self, request):
        students = students_of(request.user)
        html = render_to_string('students/records_pdf.html', {
            'students': students,
            'teacher': request.user,
            'generated_at': timezone.localtime(),
        })
        pdf = BytesIO()
        result = pisa.CreatePDF(html, dest=pdf, encoding='utf-8')
        if result.err:
            logger.error('Student records PDF failed for teacher=%s', request.user.pk)
            messages.error(request, 'No se pudo generar el PDF. Inténtalo de nuevo.')
            return HttpResponse(status=500)
        logger.info('Student records exported by teacher=%s (%s students)', request.user.pk, students.count())
        filename = f'expedientes_{timezone.localdate():%Y-%m-%d}.pdf'
        response = HttpResponse(pdf.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Cache-Control'] = 'no-store'  # personal data: do not keep it in the browser cache
        return response
