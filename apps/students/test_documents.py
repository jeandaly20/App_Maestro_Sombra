"""
The diagnosis document and the signed authorization: stored encrypted, read locally (PDF text or OCR on this
server) and checked against the student. Made-up names and documents only; the OCR tests need Tesseract.
"""
import io
import unittest
from datetime import date

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from PIL import ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from apps.accounts.models import Teacher

from . import consent, documents
from .models import Student

OCR = documents.tesseract() is not None


def digital_pdf(lines):
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    for number, line in enumerate(lines):
        page.drawString(60, 800 - 22 * number, line)
    page.save()
    return buffer.getvalue()


class DocumentCheckTests(SimpleTestCase):
    def student(self, **values):
        return Student(name='Valentina Ficticia Mora', grade='EGB_4', conditions=['DYSLEXIA'],
                       diagnosis_source='DECE_REPORT', birth_date=date(2016, 1, 1), **values)

    def test_a_matching_digital_document_passes(self):
        content = digital_pdf(['Departamento de Consejeria Estudiantil (DECE)', 'Informe de la estudiante',
                               'Valentina Ficticia Mora, 4.o de EGB', 'Presenta indicadores de dislexia.'])
        check = documents.check_document(self.student(), content, 'application/pdf')
        self.assertEqual((check['readable'], check['method']), (True, 'text'))
        self.assertTrue(check['name'] and check['institution'] and check['diagnosis'])
        self.assertIsNone(documents.document_error(check, 'Valentina Ficticia Mora'))

    def test_a_document_of_another_student_says_what_is_missing(self):
        content = digital_pdf(['Departamento de Consejeria Estudiantil (DECE)', 'Informe del estudiante',
                               'Pedro Inventado Ruiz', 'Presenta indicadores de discalculia.'])
        check = documents.check_document(self.student(), content, 'application/pdf')
        error = documents.document_error(check, 'Valentina Ficticia Mora')
        self.assertIn('el nombre del estudiante', error)
        self.assertIn('su condición NEE', error)

    def test_names_match_without_accents_or_capitals(self):
        self.assertTrue(documents.name_found('informe de valentina mora', 'Valentína Ficticia MORA'))
        self.assertFalse(documents.name_found('informe de mora', 'Valentina Ficticia Mora'))

    def test_the_file_type_is_read_from_its_content(self):
        self.assertEqual(documents.detect_type(b'%PDF-1.7'), 'application/pdf')
        self.assertEqual(documents.detect_type(b'\x89PNG\r\n\x1a\n'), 'image/png')
        self.assertIsNone(documents.detect_type(b'<html>'))


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class EncryptedStorageTests(TestCase):
    def test_the_document_is_stored_encrypted_and_read_back(self):
        teacher = Teacher.objects.create_user('docente.cifrado@maestrosombra.test', None, full_name='Docente Cifrado',
                                              accepted_terms=True, terms_accepted_at=timezone.now())
        student = Student(teacher=teacher, name='Valentina Ficticia Mora', birth_date=date(2016, 1, 1), grade='EGB_4',
                          conditions=['DYSLEXIA'], diagnosis_source='IN_EVALUATION')
        student.save()
        content = digital_pdf(['Informe ficticio de prueba'])
        documents.save_document(student, ContentFile(content), 'application/pdf', check={'readable': True})
        self.addCleanup(default_storage.delete, student.diagnosis_file)
        with default_storage.open(student.diagnosis_file, 'rb') as stored:
            self.assertNotIn(b'Informe ficticio', stored.read())  # encrypted on disk
        self.assertEqual(documents.read_document(student), content)


@unittest.skipUnless(OCR, 'Tesseract no está instalado en este equipo')
class SignedAuthorizationTests(SimpleTestCase):
    """The printed form, filled or not, read by the local OCR."""
    NAME, GRADE = 'Valentina Ficticia Mora', '4.º de EGB'

    def form_image(self):
        pdf = consent.consent_form_pdf(teacher_name='Docente Ficticio', student_name=self.NAME, grade_label=self.GRADE)
        return documents.first_page_image(pdf, 'application/pdf')

    def as_png(self, image):
        buffer = io.BytesIO()
        image.save(buffer, 'PNG')
        return buffer.getvalue()

    def sign(self, image):
        words = documents.ocr_words(image)
        draw = ImageDraw.Draw(image)
        for label in (consent.SIGNATURE_LABEL, consent.NAME_LABEL):
            left, top, right, bottom, scale = consent.find_label(words, label)
            start = right + 60 * scale
            for offset in range(0, int(140 * scale), max(1, int(6 * scale))):  # a scribble over the blank
                draw.line([(start + offset, bottom - 12 * scale), (start + offset + 18 * scale, bottom - 2 * scale)],
                          fill=0, width=max(2, int(1.5 * scale)))
        return image

    def test_the_signed_form_of_this_student_is_accepted(self):
        result = consent.check_signed(self.as_png(self.sign(self.form_image())), 'image/png',
                                      student_name=self.NAME, grade_label=self.GRADE)
        self.assertIsNone(result)

    def test_an_unsigned_form_is_refused(self):
        result = consent.check_signed(self.as_png(self.form_image()), 'image/png',
                                      student_name=self.NAME, grade_label=self.GRADE)
        self.assertIn('falta la firma', result)

    def test_the_form_of_another_student_is_refused(self):
        result = consent.check_signed(self.as_png(self.sign(self.form_image())), 'image/png',
                                      student_name='Pedro Inventado Ruiz', grade_label=self.GRADE)
        self.assertIn('Pedro Inventado Ruiz', result)
