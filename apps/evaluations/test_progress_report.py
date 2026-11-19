"""
The progress report of a student (every final evaluation in one PDF for the institution) and the APA 7 style shared
by the documents for the institution. Made-up students and classes only.
"""
import io
import zipfile
from datetime import date, datetime, timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader

from apps.accounts.models import Teacher
from apps.adaptations.models import AdaptationStatus, CurricularAdaptation
from apps.core import apa
from apps.materials.models import MaterialStatus, OriginalMaterial, Subject
from apps.students.consent import consent_form_pdf
from apps.students.models import Student

from . import progress_report
from .models import Evaluation, EvaluationStatus

NAME = 'Estudiante Ficticio Avance'
RESULTS = [  # (week, goal, level, support, observation)
    (19, 'LESS', 'NOT_YET', 'CONSTANT_SUPPORT', 'Con tiras de papel comparó dos fracciones.'),
    (20, 'EXPECTED', 'NEAR', 'FREQUENT_SUPPORT', ''),
    (21, 'MORE', 'ACHIEVES', 'OCCASIONAL_SUPPORT', 'Explicó cómo contó los cuadros.'),
]


def text_of(pdf):
    return ' '.join(' '.join((page.extract_text() or '').split()) for page in PdfReader(io.BytesIO(pdf)).pages)


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class ProgressReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.avance@maestrosombra.test', None, full_name='Docente Avance',
                                                  accepted_terms=True, terms_accepted_at=timezone.now())
        cls.student = Student(teacher=cls.teacher, name=NAME, birth_date=date(2016, 3, 1), grade='EGB_5',
                              conditions=['DYSCALCULIA'], diagnosis_source='IN_EVALUATION')
        cls.student.save()
        math = Subject.objects.get(code='MAT')
        for number, (week, goal, level, support, observation) in enumerate(RESULTS):
            material = OriginalMaterial.objects.create(
                teacher=cls.teacher, subject=math, title=f'Clase {week}', topic=f'Semana {week} · Fracciones',
                input_type='TEXT', input_text='1. Resuelve.', status=MaterialStatus.PROCESSED)
            adaptation = CurricularAdaptation.objects.create(
                material=material, student=cls.student, condition='DYSCALCULIA', conditions=['DYSCALCULIA'],
                version=1, title=f'Fracciones {week}', status=AdaptationStatus.APPROVED, content={})
            evaluation = Evaluation.objects.create(
                adaptation=adaptation, status=EvaluationStatus.FINAL, goal_attainment=goal, achievement_level=level,
                autonomy_level=support, comprehension='YES', overload='NONE', observation=observation,
                supports_applied=['material_concreto'], supports_useful=['material_concreto'])
            day = timezone.make_aware(datetime(2026, 10, 1 + 7 * number, 10))
            Evaluation.objects.filter(pk=evaluation.pk).update(created_at=day)

    def setUp(self):
        self.client.force_login(self.teacher)
        self.url = reverse('student_progress_report', args=[self.student.pk])

    def test_the_history_offers_the_progress_report(self):
        page = self.client.get(reverse('student_progress', args=[self.student.pk])).content.decode()
        self.assertIn(self.url, page)
        self.assertIn('Descargar informe de avance', page)

    def test_the_report_is_one_pdf_named_without_the_student(self):
        response = self.client.get(self.url)
        self.assertEqual((response.status_code, response['Content-Type']), (200, 'application/pdf'))
        self.assertIn(self.student.pseudonym, response['Content-Disposition'])
        self.assertNotIn('Ficticio', response['Content-Disposition'])

    def test_the_report_has_every_class_the_trend_the_observations_and_the_supports(self):
        text = text_of(self.client.get(self.url).content)
        for expected in ('Informe de Avance del Estudiante', NAME, 'Docente Avance', 'Resumen por Asignatura',
                         'Semana 19: Fracciones', 'Semana 21: Fracciones', 'Mejora observada', 'Figura 1', 'Sem. 21',
                         'Con tiras de papel comparó dos fracciones.', 'Explicó cómo contó los cuadros.',
                         'Material concreto', '3 de 3 clases', 'Firma del docente', 'No es un diagnóstico'):
            self.assertIn(expected, text)

    def test_the_period_leaves_out_the_other_classes(self):
        text = text_of(self.client.get(self.url, {'desde': '2026-10-05', 'hasta': '2026-10-31'}).content)
        self.assertIn('Del 05/10/2026 al 31/10/2026', text)
        self.assertIn('Semana 20', text)
        self.assertNotIn('Semana 19', text)
        self.assertIn('Información insuficiente', text)  # two classes: no trend

    def test_a_period_without_evaluations_or_with_wrong_dates_explains_why(self):
        back = reverse('student_progress', args=[self.student.pk])
        for query in ({'hasta': '2026-09-01'}, {'desde': 'ayer'}, {'desde': '2026-11-01', 'hasta': '2026-10-01'}):
            self.assertRedirects(self.client.get(self.url, query), back)

    def test_another_teacher_cannot_download_it(self):
        intruder = Teacher.objects.create_user('otro.avance@maestrosombra.test', None, full_name='Otro',
                                               accepted_terms=True, terms_accepted_at=timezone.now())
        self.client.force_login(intruder)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_the_student_file_includes_the_progress_report(self):
        response = self.client.get(reverse('student_file', args=[self.student.pk]))
        names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
        self.assertIn('4 - Informe de avance del estudiante.pdf', names)

    def test_without_evaluations_there_is_no_report(self):
        Evaluation.objects.update(status=EvaluationStatus.DRAFT)
        self.assertIsNone(progress_report.progress_report_pdf(self.student))
        response = self.client.get(reverse('student_file', args=[self.student.pk]))
        self.assertNotIn('4 - Informe de avance del estudiante.pdf',
                         zipfile.ZipFile(io.BytesIO(response.content)).namelist())


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class ApaDocumentsTests(TestCase):
    """The documents for the institution share the APA 7 style: Times New Roman 12 in black."""

    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.apa@maestrosombra.test', None, full_name='Docente APA',
                                                  accepted_terms=True, terms_accepted_at=timezone.now())
        Student(teacher=cls.teacher, name='Estudiante Ficticio APA', birth_date=date(2016, 3, 1), grade='EGB_5',
                conditions=['DYSCALCULIA'], diagnosis_source='IN_EVALUATION').save()

    def fonts_of(self, pdf):
        """The fonts of the text (reportlab lists its default Helvetica on every page, even when nothing uses it)."""
        found = set()
        for page in PdfReader(io.BytesIO(pdf)).pages:
            for font in (page['/Resources'].get('/Font') or {}).values():
                found.add(str(font.get_object()['/BaseFont']))
        return found - {'/Helvetica'}

    def test_the_records_use_the_apa_style(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse('student_records_pdf'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('Seudónimo', text_of(response.content))
        fonts = self.fonts_of(response.content)
        self.assertTrue(fonts and all('Times' in font for font in fonts), fonts)

    def test_the_authorization_uses_the_apa_style_on_one_page(self):
        pdf = consent_form_pdf(teacher_name='Docente APA', student_name='Estudiante Ficticio APA',
                               grade_label='5.º de EGB')
        self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages), 1)
        fonts = self.fonts_of(pdf)
        self.assertTrue(fonts and all('Times' in font for font in fonts), fonts)

    def test_margins_and_size_follow_apa(self):
        self.assertAlmostEqual(apa.MARGIN, 72, places=0)  # one inch
        self.assertEqual(apa.SIZE, 12)
        self.assertEqual(apa.styles()['body'].leading, 2 * apa.SIZE)  # double spacing
        self.assertEqual(apa.styles()['h1'].textColor, apa.BLACK)


class PeriodTests(TestCase):
    def test_period_label(self):
        start = date(2026, 9, 1)
        self.assertEqual(progress_report.period_label([], start, start + timedelta(days=29)),
                         'Del 01/09/2026 al 30/09/2026')
