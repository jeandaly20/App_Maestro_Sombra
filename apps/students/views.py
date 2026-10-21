import logging
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, InternalError, transaction
from django.db.models import Count
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, UpdateView
from xhtml2pdf import pisa

from apps.core.choices import CONDITION_DESCRIPTIONS, CONDITION_GROUPS, CONDITION_SHORT_NAMES

from .forms import StudentForm
from .models import MAX_STUDENTS_PER_TEACHER, Student

logger = logging.getLogger('maestro_sombra')


def students_of(teacher):
    return (Student.objects.filter(teacher=teacher)
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
        return Student.objects.filter(teacher=self.request.user)

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
                return super().form_valid(form)
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
