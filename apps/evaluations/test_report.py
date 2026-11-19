"""
The report of a finished evaluation, for the school's paperwork: a PDF that downloads by itself when the teacher
finishes the evaluation, and that can be downloaded again from it. Made-up students and classes only.
"""
from datetime import date

from django.core.files.base import ContentFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Teacher
from apps.adaptations.models import AdaptationStatus, CurricularAdaptation, LessonAdaptation, LessonPlan
from apps.core.context_processors import pending_download
from apps.materials.models import MaterialStatus, OriginalMaterial, Subject
from apps.students.models import Student

from . import report
from .models import Evaluation, EvaluationStatus

NAME = 'Estudiante Ficticio Prueba'
OBSERVATION = 'Resolvió dos de cinco actividades con material concreto.'


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class EvaluationReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.informe@maestrosombra.test', None, full_name='Docente Informe',
                                                  accepted_terms=True, terms_accepted_at=timezone.now())
        cls.student = Student(teacher=cls.teacher, name=NAME, birth_date=date(2016, 3, 1), grade='EGB_5',
                              conditions=['DYSCALCULIA'], diagnosis_source='IN_EVALUATION')
        cls.student.save()
        math = Subject.objects.get(code='MAT')
        cls.material = OriginalMaterial.objects.create(
            teacher=cls.teacher, subject=math, title='Áreas', topic='Semana 24 · Calculamos áreas', input_type='TEXT',
            input_text='1. Calcula el área.', status=MaterialStatus.PROCESSED)
        cls.adaptation = CurricularAdaptation.objects.create(
            material=cls.material, student=cls.student, condition='DYSCALCULIA', conditions=['DYSCALCULIA'], version=1,
            title='Calculamos áreas', status=AdaptationStatus.APPROVED,
            content={'learning_goal': 'Vas a aprender a calcular áreas.',
                     'exercise_adaptations': [{'exercise': 'Ejercicio 1 · Áreas', 'barrier': 'Números grandes.',
                                               'action': 'REDUCE', 'action_label': 'Se reduce',
                                               'adaptation': 'Hace 2 de los 4 rectángulos.',
                                               'teacher_support': 'Cuadrícula y bloques de base 10.'}]})
        lesson = LessonPlan.objects.create(
            teacher=cls.teacher, subject=math, material=cls.material, plan_file=ContentFile(b'plan', name='plan.xlsx'),
            title='Calculamos áreas', session_label='Semana 24', status=MaterialStatus.PROCESSED,
            analysis={'skill': 'M.3.2.1. Calcular áreas de figuras simples.', 'objective': 'Calcular áreas.',
                      'indicator': 'I.M.3.3.2. Calcula áreas.'})
        LessonAdaptation.objects.create(lesson=lesson, student=cls.student, condition='DYSCALCULIA',
                                        conditions=['DYSCALCULIA'], grade='NON_SIGNIFICANT', worksheet=cls.adaptation,
                                        content={'adapted_objective': 'Calcular áreas con cuadrícula.',
                                                 'plan_row': ['Reducir ejercicios repetidos.']})

    def setUp(self):
        self.client.force_login(self.teacher)

    def finish(self):
        return self.client.post(reverse('evaluation', args=[self.adaptation.pk]), {
            'action': 'final', 'goal_attainment': 'LESS', 'autonomy_level': 'FREQUENT_SUPPORT',
            'comprehension': 'DIFFICULTY', 'overload': 'MILD', 'achievement_level': 'NEAR',
            'supports_applied': ['material_concreto'], 'supports_useful': ['material_concreto'],
            'observation': OBSERVATION})

    def test_finishing_the_evaluation_downloads_the_report_once(self):
        response = self.finish()
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response['Location']).content.decode()
        url = reverse('evaluation_report', args=[self.adaptation.pk])
        self.assertIn(f'href="{url}" data-auto-download', page)
        self.assertNotIn('data-auto-download', self.client.get(response['Location']).content.decode())  # only once

    def test_the_report_is_a_pdf_named_without_the_student(self):
        self.finish()
        response = self.client.get(reverse('evaluation_report', args=[self.adaptation.pk]))
        self.assertEqual((response.status_code, response['Content-Type']), (200, 'application/pdf'))
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertIn(self.adaptation.code, response['Content-Disposition'])
        self.assertNotIn('Ficticio', response['Content-Disposition'])

    def test_the_report_has_the_class_the_results_the_observation_and_the_recommendations(self):
        self.finish()
        evaluation = Evaluation.objects.get(adaptation=self.adaptation)
        sections = report.report_sections(evaluation)
        everything = ' '.join(str(value) for _title, _kind, rows in sections for row in rows for value in row)
        titles = [title for title, _kind, _rows in sections]
        for expected in (NAME, 'Docente Informe', 'Semana 24 · Calculamos áreas', 'M.3.2.1. Calcular áreas',
                         'Calcular áreas con cuadrícula.', 'Grado 2', 'Hace 2 de los 4 rectángulos.',
                         'Menos de lo esperado (−1)', 'Está próximo a alcanzar (4 a 6)', 'Apoyo frecuente',
                         'Material concreto', OBSERVATION):
            self.assertIn(expected, everything)
        self.assertIn('Adaptaciones por Ejercicio', titles)
        self.assertEqual(titles[-1], 'Recomendaciones para la Próxima Clase')
        self.assertTrue(evaluation.local_recommendations)

    def test_a_draft_has_no_report_yet(self):
        Evaluation.objects.create(adaptation=self.adaptation, status=EvaluationStatus.DRAFT, comprehension='YES')
        response = self.client.get(reverse('evaluation_report', args=[self.adaptation.pk]))
        self.assertRedirects(response, reverse('evaluation', args=[self.adaptation.pk]))

    def test_the_finished_evaluation_offers_the_report_again(self):
        self.finish()
        page = self.client.get(reverse('evaluation', args=[self.adaptation.pk])).content.decode()
        self.assertIn(reverse('evaluation_report', args=[self.adaptation.pk]), page)
        self.assertIn('Descargar informe (PDF)', page)

    def test_another_teacher_cannot_download_it(self):
        self.finish()
        intruder = Teacher.objects.create_user('otro.informe@maestrosombra.test', None, full_name='Otro',
                                               accepted_terms=True, terms_accepted_at=timezone.now())
        self.client.force_login(intruder)
        self.assertEqual(self.client.get(reverse('evaluation_report', args=[self.adaptation.pk])).status_code, 404)

    def test_only_a_page_of_the_app_can_be_offered_for_download(self):
        request = RequestFactory().get('/')
        request.session = {'pending_download': 'https://otro-sitio.example/archivo.pdf'}
        self.assertEqual(pending_download(request), {'pending_download': None})
