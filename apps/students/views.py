import logging
import zipfile
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
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from xhtml2pdf import pisa

from apps.core.choices import CONDITION_DESCRIPTIONS, CONDITION_GROUPS, CONDITION_SHORT_NAMES, Grade, RecordStatus

from . import documents
from .consent import consent_form_pdf
from .forms import StudentForm
from .models import MAX_STUDENTS_PER_TEACHER, Student

logger = logging.getLogger('maestro_sombra')


def students_of(teacher):
    return (Student.objects.filter(teacher=teacher, record_status=RecordStatus.ACTIVE)
            .annotate(adaptation_count=Count('adaptations'))
            .order_by('created_at'))


class StudentListView(LoginRequiredMixin, TemplateView):
    """The teacher's students with everything recorded about them; registering is a page of its own."""
    template_name = 'students/student_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        students = students_of(self.request.user)
        context.update({'students': students, 'max_students': MAX_STUDENTS_PER_TEACHER,
                        'is_full': len(students) >= MAX_STUDENTS_PER_TEACHER})
        return context


class StudentPageMixin(LoginRequiredMixin):
    """Shared by the register and edit views: the student's record form."""
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
        context.update({
            'max_students': MAX_STUDENTS_PER_TEACHER,
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
                replaced = []
                if form.cleaned_data.get('consent_document'):
                    replaced.append(documents.save_consent(form.instance, form.cleaned_data['consent_document'],
                                                           form.consent_type))
                if form.cleaned_data.get('diagnosis_document'):
                    replaced.append(documents.save_document(form.instance, form.cleaned_data['diagnosis_document'],
                                                            form.document_type, form.document_check))
                elif form.remove_document:
                    # Back to "in evaluation": the stored document no longer applies
                    documents.delete_document(form.instance, save=False)
                elif form.document_check:
                    form.instance.diagnosis_check = form.document_check  # read again by the form: the data changed
                response = super().form_valid(form)
            for path in filter(None, replaced):
                default_storage.delete(path)
            return response
        except (IntegrityError, InternalError):
            # The PostgreSQL trigger caught a 4th student (e.g. two tabs saving at once)
            form.add_error(None, f'Un docente puede registrar como máximo {MAX_STUDENTS_PER_TEACHER} estudiantes.')
            return self.form_invalid(form)


class StudentCreateView(StudentPageMixin, CreateView):

    def get(self, request, *args, **kwargs):
        if students_of(request.user).count() >= MAX_STUDENTS_PER_TEACHER:
            messages.info(request, f'Ya registraste el máximo de {MAX_STUDENTS_PER_TEACHER} estudiantes. Da de baja a '
                                   'uno desde su ficha para registrar a otro.')
            return redirect('student_list')
        return super().get(request, *args, **kwargs)

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
        student.save(update_fields=['record_status', 'updated_at'] + documents.DOCUMENT_FIELDS)
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


class StudentConsentView(LoginRequiredMixin, View):
    """Opens the signed authorization, decrypted, only for the student's teacher. Every opening is logged."""

    def get(self, request, pk):
        student = get_object_or_404(Student, pk=pk, teacher=request.user, record_status=RecordStatus.ACTIVE)
        if not student.has_signed_consent:
            raise Http404
        logger.info('Signed authorization opened: %s by teacher=%s', student.pseudonym, request.user.pk)
        response = HttpResponse(documents.read_consent(student), content_type=student.consent_file_type)
        response['Content-Disposition'] = f'inline; filename="{documents.consent_download_name(student)}"'
        response['Cache-Control'] = 'private, no-store'
        return response


class StudentConsentFormView(LoginRequiredMixin, View):
    """
    The printable authorization for the representative to sign, with the student's name and grade as typed in the
    record form, so the signed copy can be checked against them. POST: the name never travels in a URL.
    """

    def post(self, request):
        name = ' '.join(request.POST.get('name', '').split())[:120]
        grade = request.POST.get('grade', '')
        if len(name) < 3 or grade not in Grade.values:
            messages.error(request, 'Escribe el nombre y elige el grado del estudiante antes de descargar la '
                                    'autorización: el formato sale con esos datos.')
            return redirect(request.POST.get('back') if url_has_allowed_host_and_scheme(
                request.POST.get('back'), allowed_hosts={request.get_host()}) else 'student_create')
        pdf = consent_form_pdf(teacher_name=request.user.full_name, student_name=name, grade_label=Grade(grade).label)
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="autorizacion-representante.pdf"'
        response['Cache-Control'] = 'private, no-store'
        return response


def records_pdf(students, teacher, title):
    """The students' records as PDF bytes, or None if xhtml2pdf fails."""
    html = render_to_string('students/records_pdf.html', {
        'students': students, 'teacher': teacher, 'generated_at': timezone.localtime(), 'title': title,
    })
    pdf = BytesIO()
    return None if pisa.CreatePDF(html, dest=pdf, encoding='utf-8').err else pdf.getvalue()


class StudentFileView(LoginRequiredMixin, View):
    """
    One student's complete file as a ZIP: the record (PDF) plus the diagnosis document and the signed authorization,
    decrypted, for any review. Personal data of a minor: only their teacher, every download is logged.
    """

    def get(self, request, pk):
        student = get_object_or_404(students_of(request.user), pk=pk)
        record = records_pdf([student], request.user, f'Expediente de {student.name}')
        if record is None:
            logger.error('Student file PDF failed for %s', student.pseudonym)
            messages.error(request, 'No se pudo generar el expediente. Inténtalo de nuevo.')
            return redirect('student_list')
        archive = BytesIO()
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
            zipped.writestr('1 - Ficha del estudiante.pdf', record)
            if student.has_document:
                extension = documents.EXTENSIONS.get(student.diagnosis_file_type, 'bin')
                zipped.writestr(f'2 - Documento de respaldo ({student.get_diagnosis_source_display()}).{extension}'
                                .replace('/', '-'), documents.read_document(student))
            if student.has_signed_consent:
                extension = documents.EXTENSIONS.get(student.consent_file_type, 'bin')
                zipped.writestr(f'3 - Autorizacion firmada del representante.{extension}', documents.read_consent(student))
        logger.info('Student file downloaded: %s by teacher=%s', student.pseudonym, request.user.pk)
        response = HttpResponse(archive.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="expediente-{student.pseudonym}.zip"'
        response['Cache-Control'] = 'private, no-store'
        return response


class StudentRecordsPdfView(LoginRequiredMixin, View):
    """Downloads the teacher's student records as a PDF (personal data: teacher only)."""

    def get(self, request):
        students = students_of(request.user)
        pdf = records_pdf(students, request.user, 'Expedientes de estudiantes con NEE')
        if pdf is None:
            logger.error('Student records PDF failed for teacher=%s', request.user.pk)
            messages.error(request, 'No se pudo generar el PDF. Inténtalo de nuevo.')
            return HttpResponse(status=500)
        logger.info('Student records exported by teacher=%s (%s students)', request.user.pk, students.count())
        filename = f'expedientes_{timezone.localdate():%Y-%m-%d}.pdf'
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Cache-Control'] = 'no-store'  # personal data: do not keep it in the browser cache
        return response
