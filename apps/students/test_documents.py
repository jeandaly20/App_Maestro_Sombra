"""Diagnosis document: optional, authorized, encrypted, owner-only, deleted with the student."""
from datetime import date

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Teacher

from .models import Student
from .tests import PASSWORD, student_data

PDF = b'%PDF-1.4\n% informe psicopedagogico de prueba\n%%EOF'


def pdf_file(content=PDF, name='informe.pdf'):
    return SimpleUploadedFile(name, content, content_type='application/pdf')


class DiagnosisDocumentTests(TestCase):

    def setUp(self):
        self.teacher = Teacher.objects.create_user('ana@correo.com', PASSWORD, full_name='Ana Pérez')
        self.client.force_login(self.teacher)
        self.url = reverse('student_list')

    def register(self, **changes):
        data = student_data(diagnosis_document=pdf_file(), diagnosis_consent='on')
        data.update(changes)
        return self.client.post(self.url, data)

    def test_document_is_stored_encrypted_and_only_its_teacher_opens_it(self):
        self.register()
        student = Student.objects.get()
        self.assertTrue(student.has_document)
        self.assertIsNotNone(student.diagnosis_consent_at)
        self.assertEqual(student.diagnosis_status, 'Documento adjunto')
        with default_storage.open(student.diagnosis_file, 'rb') as stored:
            self.assertNotIn(b'%PDF', stored.read())  # unreadable on disk
        response = self.client.get(reverse('student_document', args=[student.pk]))
        self.assertEqual(response.content, PDF)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        intruder = Teacher.objects.create_user('luis@correo.com', PASSWORD, full_name='Luis Mora')
        self.client.force_login(intruder)
        self.assertEqual(self.client.get(reverse('student_document', args=[student.pk])).status_code, 404)

    def test_document_needs_the_representatives_authorization(self):
        response = self.register(diagnosis_consent='')
        self.assertContains(response, 'necesitas la autorización del representante')
        self.assertFalse(Student.objects.exists())

    def test_a_disguised_file_is_rejected(self):
        response = self.register(diagnosis_document=pdf_file(b'MZ\x90\x00 programa', 'informe.pdf'))
        self.assertContains(response, 'Sube el documento en PDF, JPG o PNG.')
        self.assertFalse(Student.objects.exists())

    def test_document_is_not_allowed_while_in_evaluation(self):
        response = self.register(diagnosis_source='IN_EVALUATION')
        self.assertContains(response, 'indica cuál es en lugar de')

    def test_leave_and_deletion_remove_the_document(self):
        self.register()
        student = Student.objects.get()
        path = student.diagnosis_file
        self.client.post(reverse('student_deactivate', args=[student.pk]))
        student.refresh_from_db()
        self.assertFalse(student.has_document)
        self.assertFalse(default_storage.exists(path))
        # Deleting a student with a document also deletes the file
        self.register(name='Sofía Ramos')
        other = Student.objects.get(record_status='ACTIVE')
        other_path = other.diagnosis_file
        other.delete()
        self.assertFalse(default_storage.exists(other_path))

    def test_back_to_in_evaluation_removes_the_document(self):
        self.register()
        student = Student.objects.get()
        path = student.diagnosis_file
        data = student_data(diagnosis_source='IN_EVALUATION', diagnosis_date='')
        self.client.post(reverse('student_edit', args=[student.pk]), data)
        student.refresh_from_db()
        self.assertTrue(student.is_diagnosis_pending)
        self.assertFalse(student.has_document)
        self.assertFalse(default_storage.exists(path))

    def test_teacher_can_remove_the_document(self):
        self.register()
        student = Student.objects.get()
        self.client.post(reverse('student_edit', args=[student.pk]), student_data(remove_document='on'))
        student.refresh_from_db()
        self.assertFalse(student.has_document)
        self.assertEqual(student.diagnosis_source, 'UDAI_REPORT')  # the declaration stays
        self.assertEqual(student.birth_date, date(2016, 5, 1))


def text_pdf(*lines):
    """A real PDF with text, like a digital UDAI report."""
    from io import BytesIO

    from reportlab.pdfgen import canvas
    buffer = BytesIO()
    page = canvas.Canvas(buffer)
    for number, line in enumerate(lines):
        page.drawString(72, 760 - number * 18, line)
    page.save()
    return buffer.getvalue()


class DocumentCheckTests(TestCase):
    """The local reading flags mismatches; it never sends the document anywhere."""

    def setUp(self):
        self.teacher = Teacher.objects.create_user('ana@correo.com', PASSWORD, full_name='Ana Pérez')
        self.client.force_login(self.teacher)
        self.url = reverse('student_list')

    def register(self, content, name='informe.pdf', content_type='application/pdf', **changes):
        data = student_data(diagnosis_document=SimpleUploadedFile(name, content, content_type=content_type),
                            diagnosis_consent='on', diagnosis_date='')
        data.update(changes)
        return self.client.post(self.url, data)

    def test_matching_report(self):
        self.register(text_pdf('Unidad Distrital de Apoyo a la Inclusion - UDAI 09D01',
                               'Informe psicopedagogico de MATEO BENITEZ',
                               'Conclusion: dislexia y discalculia.'))
        student = Student.objects.get()
        self.assertIsNone(student.diagnosis_date)  # the attached document makes the date optional
        self.assertEqual(student.document_check[0], 'ok')
        self.assertContains(self.client.get(self.url), 'Coincide el nombre, la institución y el diagnóstico.')

    def test_report_of_another_child_is_flagged(self):
        self.register(text_pdf('UDAI 09D01 - Informe psicopedagogico', 'Estudiante: Daniel Vera',
                               'Diagnostico: trastorno del espectro autista.'))
        level, text = Student.objects.get().document_check
        self.assertEqual(level, 'warn')
        self.assertIn('el nombre del estudiante', text)
        self.assertIn('el diagnóstico', text)
        self.assertNotIn('la institución', text)

    def test_photo_needs_manual_review(self):
        self.register(b'\x89PNG\r\n\x1a\n' + b'\x00' * 64, name='foto.png', content_type='image/png')
        self.assertEqual(Student.objects.get().document_check[0], 'manual')

    def test_date_is_required_without_a_document(self):
        response = self.client.post(self.url, student_data(diagnosis_date=''))
        self.assertContains(response, 'o adjunta el documento')

    def test_check_is_redone_when_the_name_changes(self):
        self.register(text_pdf('UDAI - Informe de Mateo Benitez', 'dislexia'))
        student = Student.objects.get()
        self.assertEqual(student.document_check[0], 'ok')
        self.client.post(reverse('student_edit', args=[student.pk]),
                         student_data(name='Sofía Ramos', diagnosis_date=''))
        student.refresh_from_db()
        self.assertEqual(student.document_check[0], 'warn')
