"""
"Adaptar clase" with plans in Word and in PDF, and the adapted class given back in the teacher's format
(Excel, PDF or Word). Made-up plans and students only.
"""
import io
from datetime import date
from unittest import mock

import openpyxl
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from docx import Document
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from apps.accounts.models import Teacher
from apps.adaptations import ai, lesson_plan, services
from apps.adaptations.models import LessonPlan
from apps.adaptations.tests import FakeGemini
from apps.adaptations.views import class_sheet
from apps.core.test_integration import plan_bytes
from apps.materials.models import OriginalMaterial, Subject
from apps.students.models import Student

SESSIONS = [('Sesión 1', 'Las partes de la planta'), ('Sesión 2', 'La raíz y el tallo'),
            ('Sesión 3', 'Las hojas y la flor')]


def plan_lines():
    lines = ['Semana 1']
    for label, title in SESSIONS:
        lines += [f'{label}. Título: {title}', f'Objetivo: Identificar {title.lower()} y su función.',
                  'Actividades: observar una planta, dibujar y rotular.']
    return lines


def word_plan():
    document = Document()
    document.add_paragraph('PLANIFICACIÓN SEMANAL · Ciencias Naturales · 4.º EGB')
    document.add_paragraph('Semana 1')
    table = document.add_table(rows=0, cols=3)
    for label, title in SESSIONS:
        for cells in ([label, 'Título:', title], ['Objetivo:', f'Identificar {title.lower()}.', ''],
                      ['Actividades:', 'Observar, dibujar y rotular.', '']):
            row = table.add_row().cells
            for cell, text in zip(row, cells):
                cell.text = text
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def pdf_plan():
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    y = 800
    for line in plan_lines():
        page.drawString(60, y, line)
        y -= 22
    page.save()
    return buffer.getvalue()


class PlanFormatTests(SimpleTestCase):
    def test_a_word_plan_is_read_session_by_session(self):
        found = lesson_plan.sessions(word_plan())
        self.assertEqual([s['title'] for s in found], [title for _, title in SESSIONS])

    def test_a_digital_pdf_plan_is_read_session_by_session(self):
        found = lesson_plan.sessions(pdf_plan())
        self.assertEqual([s['title'] for s in found], [title for _, title in SESSIONS])

    def test_a_scanned_pdf_is_refused_with_a_clear_message(self):
        buffer = io.BytesIO()
        Image.new('RGB', (600, 800), 'white').save(buffer, 'PDF')
        with self.assertRaisesMessage(lesson_plan.PlanError, 'escaneado'):
            lesson_plan.sessions(buffer.getvalue())

    def test_a_damaged_file_is_refused(self):
        with self.assertRaises(lesson_plan.PlanError):
            lesson_plan.sessions(b'PK\x03\x04 archivo roto')


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class AdaptedClassFormatTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        teacher = Teacher.objects.create_user('docente.formatos@maestrosombra.test', None, full_name='Docente Formatos',
                                              accepted_terms=True, terms_accepted_at=timezone.now())
        student = Student(teacher=teacher, name='Estudiante Formatos', birth_date=date(2016, 1, 1), grade='EGB_4',
                          conditions=['ADHD'], diagnosis_source='IN_EVALUATION')
        student.save()
        science = Subject.objects.get(code='CIE')
        plan = plan_bytes()
        week = lesson_plan.weeks(lesson_plan.sessions(plan))[0]
        book = OriginalMaterial.objects.create(teacher=teacher, subject=science, title='Plantas', topic='Semana 1',
                                               input_type='TEXT', input_text='La planta tiene raíz. 1. Observa.')
        lesson = LessonPlan.objects.create(
            teacher=teacher, subject=science, material=book, plan_file=ContentFile(plan, name='plan.xlsx'),
            plan_file_name='plan.xlsx', sheet=week['sheet'], session_row=week['row'], session_label='Semana 1',
            title=week['title'], session_text=lesson_plan.session_text(plan, week['sheet'], week['row'],
                                                                       week.get('last_row')))
        with mock.patch.object(ai, 'generate_json', FakeGemini()):
            services.process_lesson(lesson.pk, [(student.pk, ['ADHD'])], {}, check_topic=False)
        cls.adaptation = lesson.adaptations.get()

    def test_the_adapted_class_comes_back_in_excel_pdf_and_word(self):
        sheet = class_sheet(self.adaptation)
        content, extension, _type = lesson_plan.adapted_class([sheet], 'xlsx')
        self.assertEqual(extension, '.xlsx')
        cells = ' '.join(str(c.value) for row in openpyxl.load_workbook(io.BytesIO(content)).active.iter_rows()
                         for c in row if c.value)
        self.assertIn(self.adaptation.student.pseudonym, cells)

        content, extension, _type = lesson_plan.adapted_class([sheet], 'pdf')
        self.assertEqual((extension, content[:5]), ('.pdf', b'%PDF-'))

        content, extension, _type = lesson_plan.adapted_class([sheet], 'docx')
        text = ' '.join(cell.text for table in Document(io.BytesIO(content)).tables for row in table.rows
                        for cell in row.cells)
        self.assertEqual(extension, '.docx')
        self.assertIn(self.adaptation.student.pseudonym, text)

    def test_the_file_never_carries_the_students_name(self):
        sheet = class_sheet(self.adaptation)
        for kind in ('xlsx', 'pdf', 'docx'):
            content, _extension, _type = lesson_plan.adapted_class([sheet], kind)
            self.assertNotIn(b'Estudiante Formatos', content, kind)
