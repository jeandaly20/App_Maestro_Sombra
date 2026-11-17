"""
The diagnosis report may come inside a ZIP with several reports: only the one of this student is found and kept;
the other children's reports are read in memory and never stored. Made-up reports and names only.
"""
import io
import zipfile
from types import SimpleNamespace
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from reportlab.pdfgen import canvas

from . import documents
from .forms import StudentForm


def report(name, condition='dislexia'):
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    page.drawString(72, 780, 'Unidad Distrital de Apoyo a la Inclusion - UDAI')
    page.drawString(72, 750, f'Informe psicopedagogico de {name}')
    page.drawString(72, 720, f'Conclusion: el estudiante presenta {condition}.')
    page.save()
    return buffer.getvalue()


def zipped(files, password_flag=False):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    content = bytearray(buffer.getvalue())
    if password_flag:  # the first entry marked as encrypted in the central directory, as in a ZIP with password
        content[content.find(b'PK\x01\x02') + 8] |= 0x1
    return bytes(content)


STUDENT = SimpleNamespace(name='Ximena Quilumba Tixe', diagnosis_source='UDAI_REPORT', conditions=['DYSLEXIA'])
REPORTS = {'informes/bayron.pdf': report('Bayron Chiluisa Pazmino'), 'informes/ximena.pdf': report('Ximena Quilumba Tixe'),
           'informes/notas.txt': b'no es un informe', '__MACOSX/._ximena.pdf': b'basura'}


class FindInZipTests(SimpleTestCase):
    def test_the_report_of_this_student_is_found(self):
        content, kind, check, name = documents.find_in_zip(STUDENT, zipped(REPORTS))
        self.assertEqual((kind, name), ('application/pdf', 'ximena.pdf'))
        self.assertEqual(content, REPORTS['informes/ximena.pdf'])
        self.assertTrue(check['name'] and check['institution'] and check['diagnosis'])

    def test_the_file_name_does_not_matter(self):
        files = {'a.pdf': report('Bayron Chiluisa Pazmino'), 'b.pdf': report('Ximena Quilumba Tixe')}
        self.assertEqual(documents.find_in_zip(STUDENT, zipped(files))[3], 'b.pdf')

    def test_no_report_of_this_student_is_refused(self):
        with self.assertRaisesMessage(documents.ZipError, 'Ninguno de los 1 documentos del ZIP coincide'):
            documents.find_in_zip(STUDENT, zipped({'bayron.pdf': report('Bayron Chiluisa Pazmino')}))

    def test_a_zip_without_documents_damaged_or_with_password_is_refused(self):
        with self.assertRaisesMessage(documents.ZipError, 'no tiene documentos en PDF, JPG o PNG'):
            documents.zip_documents(zipped({'notas.txt': b'texto'}))
        with self.assertRaisesMessage(documents.ZipError, 'dañado'):
            documents.zip_documents(b'PK\x03\x04 roto')
        with self.assertRaisesMessage(documents.ZipError, 'contraseña'):
            documents.zip_documents(zipped({'x.pdf': report('Ximena Quilumba Tixe')}, password_flag=True))

    def test_a_zip_bomb_is_refused_before_reading(self):
        with mock.patch.object(documents, 'MAX_ZIP_TOTAL_MB', 0):
            with self.assertRaisesMessage(documents.ZipError, 'supera'):
                documents.zip_documents(zipped(REPORTS))


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class FormTests(TestCase):
    def form(self, upload):
        data = {'name': STUDENT.name, 'birth_date': '2016-03-01', 'grade': 'EGB_5', 'conditions': ['DYSLEXIA'],
                'diagnosis_source': 'UDAI_REPORT', 'diagnosis_date': '', 'notes': ''}
        files = {'diagnosis_document': upload,
                 'consent_document': SimpleUploadedFile('firma.pdf', report('x'), content_type='application/pdf')}
        with mock.patch('apps.students.forms.check_signed', return_value=None):
            form = StudentForm(data, files)
            form.is_valid()
        return form

    def test_the_form_accepts_a_zip_and_keeps_only_this_student_report(self):
        self.assertIn('.zip', StudentForm().fields['diagnosis_document'].widget.attrs['accept'])
        form = self.form(SimpleUploadedFile('informes.zip', zipped(REPORTS), content_type='application/zip'))
        self.assertNotIn('diagnosis_document', form.errors)
        chosen = form.cleaned_data['diagnosis_document']
        chosen.seek(0)
        self.assertEqual(chosen.read(), REPORTS['informes/ximena.pdf'])
        self.assertEqual(form.document_type, 'application/pdf')
        self.assertTrue(form.document_check['name'])

    def test_the_form_explains_when_the_zip_has_no_report_of_this_student(self):
        form = self.form(SimpleUploadedFile('informes.zip', zipped({'b.pdf': report('Bayron Chiluisa Pazmino')})))
        self.assertIn('Ninguno de los 1 documentos del ZIP coincide', form.errors['diagnosis_document'][0])
