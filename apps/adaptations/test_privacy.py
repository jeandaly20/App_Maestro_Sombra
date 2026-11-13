"""
Task 3.1: privacy check of documents before they reach Gemini, and the individual consent for the profile.
Gemini is simulated; made-up people and documents built in memory (no real data, no external calls).
"""
import base64
import io
from datetime import date
from itertools import combinations
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from docx import Document
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from apps.accounts.models import Teacher
from apps.core.choices import INTELLECTUAL_DISABILITY, LEARNING_DISORDERS, Condition
from apps.materials.models import MaterialStatus, OriginalMaterial, Subject
from apps.students import documents
from apps.students.models import Student, StudentConsent, Treatment, ai_profile_allowed

from . import ai, document_privacy as privacy, pedagogical_relevance as engine, services
from .forms import AdaptMaterialForm
from .models import LessonPlan
from .tests import FakeGemini

NAME = 'Zoila Quintanilla'
CEDULA = '0912345678'
EMAIL = 'zoila.q@correo.com'


def docx_bytes():
    document = Document()
    document.core_properties.author = 'Otra Persona Autora'
    document.sections[0].header.paragraphs[0].text = f'Estudiante: {NAME}'
    document.sections[0].footer.paragraphs[0].text = f'Correo: {EMAIL}'
    document.add_paragraph('Subraya los sustantivos del texto: perro, mesa, Quito.')
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = f'Cédula: {CEDULA}'
    table.rows[0].cells[1].text = 'Escribe tres sustantivos comunes.'
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def pdf_bytes(lines, author='', photo=False):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    if author:
        pdf.setAuthor(author)
    for number, line in enumerate(lines):
        pdf.drawString(60, 780 - number * 22, line)
    if photo:
        image = Image.new('RGB', (200, 120), (200, 80, 80))
        pdf.drawImage(ImageReader(image), 60, 500, 200, 120)
    pdf.save()
    return buffer.getvalue()


def scanned_pdf_bytes():
    image = Image.new('RGB', (1240, 1754), 'white')
    ImageDraw.Draw(image).text((100, 100), f'Nombre: {NAME}', fill='black')
    buffer = io.BytesIO()
    image.save(buffer, 'PDF')
    return buffer.getvalue()


def jpeg_bytes():
    buffer = io.BytesIO()
    Image.new('RGB', (300, 200), 'white').save(buffer, 'JPEG')
    return buffer.getvalue()


def black_pixels(image):
    return sum(1 for pixel in image.convert('L').getdata() if pixel < 20)


CLEAN_LESSON = ['1. Reparte 12 caramelos entre 3 ninos.', '2. Calcula 24 x 3.', '3. Compara 1/2 y 1/4.']


class DocumentInspectionTests(TestCase):
    def test_01_text_without_identifiers(self):
        result = privacy.inspect_text('Subraya los sustantivos: perro, mesa y Quito. En 1492 llegó Colón.')
        self.assertEqual(result.status, privacy.APTO)
        self.assertIn('En 1492 llegó Colón', result.text)

    def test_02_text_with_names(self):
        result = privacy.inspect_text(f'Nombre: {NAME}\nDocente: Lcda. María Pérez\n{NAME.split()[0]} lee el cuento.',
                                      names=[NAME])
        self.assertEqual(result.status, privacy.DEPURADO)
        for private in ('Zoila', 'Quintanilla', 'María Pérez'):
            self.assertNotIn(private, result.text)
        self.assertIn('lee el cuento', result.text)
        self.assertNotIn('Zoila', result.message)  # the message never repeats the data

    def test_03_text_with_identification_numbers(self):
        result = privacy.inspect_text(f'Cédula: {CEDULA}. RUC 0912345678001. Pasaporte: AB123456. Llamar al 0991234567.')
        self.assertEqual(result.status, privacy.DEPURADO)
        for private in (CEDULA, '0912345678001', 'AB123456', '0991234567'):
            self.assertNotIn(private, result.text)

    def test_04_word_with_identifiable_information(self):
        result = privacy.inspect_file(docx_bytes(), names=[NAME])
        self.assertEqual(result.status, privacy.DEPURADO)
        for private in (NAME, CEDULA, EMAIL, 'Otra Persona Autora'):
            self.assertNotIn(private, result.text)
        self.assertIn('Escribe tres sustantivos comunes.', result.text)  # body and tables are kept
        self.assertEqual(result.images, [])  # only the cleaned text is sent, never the Word itself
        self.assertEqual(result.findings['metadatos'], 1)

    def test_05_digital_pdf_is_cleaned_and_redacted(self):
        raw = pdf_bytes([f'Nombre: {NAME}   Correo: {EMAIL}', *CLEAN_LESSON], photo=True)
        result = privacy.inspect_file(raw, names=[NAME])
        self.assertEqual(result.status, privacy.DEPURADO)
        self.assertNotIn(EMAIL, result.text)
        self.assertNotIn('Quintanilla', result.text)
        self.assertIn('Calcula 24 x 3.', result.text)
        self.assertEqual(len(result.images), 1)
        clean = privacy.inspect_file(pdf_bytes(CLEAN_LESSON))
        self.assertGreater(black_pixels(result.images[0]), black_pixels(clean.images[0]) + 500)  # covered boxes
        self.assertEqual(result.findings['imagenes'], 1)  # the embedded photo was blanked out

    def test_06_scanned_pdf_is_blocked(self):
        result = privacy.inspect_file(scanned_pdf_bytes(), names=[NAME])
        self.assertEqual(result.status, privacy.BLOQUEADO)
        self.assertEqual((result.text, result.images), ('', []))
        self.assertIn('escaneado', result.message)

    def test_07_photo_is_blocked(self):
        result = privacy.inspect_file(jpeg_bytes())
        self.assertEqual(result.status, privacy.BLOQUEADO)
        self.assertIn('las fotos no se envían a la IA', result.message)

    def test_08_personal_metadata_never_leaves(self):
        result = privacy.inspect_file(pdf_bytes(CLEAN_LESSON, author=NAME), names=[NAME])
        self.assertEqual(result.status, privacy.DEPURADO)
        self.assertEqual(result.findings['metadatos'], 1)
        self.assertNotIn(NAME, result.text)

    def test_09_extraction_failure_blocks(self):
        result = privacy.inspect_file(b'%PDF-1.4 archivo roto sin contenido')
        self.assertEqual(result.status, privacy.BLOQUEADO)
        self.assertEqual((result.text, result.images), ('', []))

    def test_10_any_error_blocks_and_nothing_is_sent(self):
        with mock.patch.object(privacy, '_inspect_pdf', side_effect=RuntimeError('fallo interno')):
            result = privacy.inspect_file(pdf_bytes(CLEAN_LESSON))
        self.assertEqual(result.status, privacy.BLOQUEADO)
        self.assertNotIn('fallo interno', result.message)


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class PrivacyInTheAdaptationFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.privacidad@maestrosombra.test', None,
                                                  full_name='Docente de Prueba', accepted_terms=True,
                                                  terms_accepted_at=timezone.now())
        cls.student = Student(teacher=cls.teacher, name=NAME, birth_date=date(2016, 3, 1), grade='EGB_5',
                              conditions=['DYSCALCULIA', 'ADHD'], diagnosis_source='IN_EVALUATION',
                              learning_barriers='Le cuesta mantener la atención en actividades largas.',
                              pedagogical_supports='Pausas programadas e instrucciones breves.')
        cls.student.save()
        cls.math = Subject.objects.get(code='MAT')

    def tearDown(self):
        # The database is rolled back after each test, the files on disk are not: delete them here
        from . import figures
        for material in OriginalMaterial.objects.filter(teacher=self.teacher):
            figures.delete_figures(material)
            if material.file:
                material.file.delete(save=False)
        for lesson in LessonPlan.objects.filter(teacher=self.teacher):
            for stored in (lesson.guide_file, lesson.plan_file):
                if stored:
                    stored.delete(save=False)

    def material(self, raw, input_type='PDF', name='guia.pdf'):
        material = OriginalMaterial(teacher=self.teacher, subject=self.math, title='Guía', topic='Repartos',
                                    input_type=input_type)
        material.file.save(name, ContentFile(raw), save=False)
        material.save()
        return material

    def adapt(self, material):
        fake = FakeGemini()
        with mock.patch.object(ai, 'generate_json', fake):
            services.process_material(material.pk, [(self.student.pk, self.student.conditions)], {self.student.pk: []})
        material.refresh_from_db()
        return fake

    def test_07b_a_photo_is_not_offered_and_a_scan_is_refused_at_upload(self):
        form = AdaptMaterialForm({'students': [str(self.student.pk)], 'subject': self.math.pk, 'topic': 'Repartos',
                                  'input_type': 'PDF', f'rules_{self.student.pk}': []},
                                 {'file': SimpleUploadedFile('guia.pdf', scanned_pdf_bytes())}, teacher=self.teacher)
        self.assertFalse(form.is_valid())
        self.assertIn('escaneado', form.errors['file'][0])
        self.assertNotIn('PHOTO', [value for value, _label in form.fields['input_type'].choices])

    def test_10b_a_blocked_material_never_reaches_gemini(self):
        material = self.material(jpeg_bytes(), input_type='PHOTO', name='foto.jpg')
        fake = self.adapt(material)
        self.assertEqual(material.status, MaterialStatus.ERROR)
        self.assertTrue(material.ai_error.startswith(ai.PRIVACY_BLOCK))
        self.assertEqual(fake.requests, [])

    def test_15_no_names_or_personal_data_in_gemini_requests(self):
        raw = pdf_bytes([f'Nombre: {NAME}', f'Cedula: {CEDULA}  Correo: {EMAIL}', *CLEAN_LESSON], author=NAME)
        material = self.material(raw)
        fake = self.adapt(material)
        self.assertEqual(material.status, MaterialStatus.PROCESSED, material.ai_error)
        everything = fake.everything()
        for private in ('Zoila', 'Quintanilla', CEDULA, EMAIL, '2016-03-01', 'Pausas programadas',
                        'mantener la atención en actividades largas'):
            self.assertNotIn(private, everything)
        self.assertIn('Antes de enviar el material a la IA se quitaron', material.ai_analysis['privacy']['message'])

    def test_16_original_documents_are_never_sent(self):
        raw = pdf_bytes([f'Nombre: {NAME}', *CLEAN_LESSON], photo=True)
        material = self.material(raw)
        fake, sent = FakeGemini(), []

        def recorder(system, parts, schema):
            sent.extend(part['inlineData']['data'] for part in parts if 'inlineData' in part)
            return fake(system, parts, schema)

        with mock.patch.object(ai, 'generate_json', recorder), \
                mock.patch.object(documents, 'read_document', side_effect=AssertionError('diagnóstico leído')), \
                mock.patch.object(documents, 'read_consent', side_effect=AssertionError('autorización leída')):
            services.process_material(material.pk, [(self.student.pk, self.student.conditions)], {self.student.pk: []})
        material.refresh_from_db()
        self.assertEqual(material.status, MaterialStatus.PROCESSED, material.ai_error)
        self.assertEqual(len(sent), 1)  # one redacted page image rendered here: no metadata, photo blanked out
        self.assertNotIn(base64.b64encode(raw).decode()[:300], sent[0])  # the original file never leaves
        page = Image.open(io.BytesIO(base64.b64decode(sent[0])))
        self.assertGreater(black_pixels(page), 300)  # the name is covered in what was sent

    def test_17_material_adaptation_works_with_a_word_file(self):
        material = self.material(docx_bytes(), input_type='WORD', name='guia.docx')
        fake = self.adapt(material)
        self.assertEqual(material.status, MaterialStatus.PROCESSED, material.ai_error)
        self.assertEqual(material.adaptations.count(), 1)
        self.assertEqual(material.ai_analysis['privacy']['status'], privacy.DEPURADO)
        self.assertNotIn(CEDULA, fake.everything())

    def test_18_class_adaptation_works_and_skips_an_unsafe_guide(self):
        book = self.material(pdf_bytes(['Pagina del libro', *CLEAN_LESSON]))
        lesson = LessonPlan.objects.create(teacher=self.teacher, subject=self.math, material=book,
                                           plan_file=ContentFile(b'plan', name='plan.xlsx'), session_label='Semana 1',
                                           title='Repartos', session_text=f'Semana 1. Docente: Lcda. Ana Mora. {EMAIL}')
        lesson.guide_file.save('guia.pdf', ContentFile(scanned_pdf_bytes()), save=True)
        fake = FakeGemini()
        with mock.patch.object(ai, 'generate_json', fake):
            services.process_lesson(lesson.pk, [(self.student.pk, self.student.conditions)], {self.student.pk: []},
                                    check_topic=False)
        lesson.refresh_from_db()
        self.assertEqual(lesson.status, MaterialStatus.PROCESSED, lesson.ai_error)
        self.assertIn('La guía del docente no se envió a la IA', lesson.analysis.get('privacy_note', ''))
        for private in (EMAIL, 'Ana Mora', 'Zoila'):
            self.assertNotIn(private, fake.everything())

    def test_19_the_23_combinations_keep_the_profile_out_without_authorization(self):
        supported = [list(c) for size in range(1, 7) for c in combinations([c.value for c in Condition], size)
                     if not (set(c) & set(LEARNING_DISORDERS) and set(c) & set(INTELLECTUAL_DISABILITY))
                     and not set(INTELLECTUAL_DISABILITY) <= set(c)]
        self.assertEqual(len(supported), 23)
        profile = {'learning_barriers': 'Le cuesta mantener la atención.', 'pedagogical_supports': 'Pausas programadas.'}
        for conditions in supported:
            with self.subTest(conditions=conditions):
                hidden = engine.analyze(conditions=conditions, profile=profile, subject_code='MAT', subject_name='Matemática',
                                        topic='Repartos', activity_text=' '.join(CLEAN_LESSON), exercise_count=5)
                shared = engine.analyze(conditions=conditions, profile=profile, subject_code='MAT', subject_name='Matemática',
                                        topic='Repartos', activity_text=' '.join(CLEAN_LESSON), exercise_count=5,
                                        share_profile=True)
                self.assertNotIn('Perfil pedagógico del estudiante', hidden['ai_prompt'])
                self.assertIn('Perfil pedagógico del estudiante (categorías', shared['ai_prompt'])
                for text in profile.values():
                    self.assertNotIn(text, shared['ai_prompt'])  # never the teacher's words, even when allowed
