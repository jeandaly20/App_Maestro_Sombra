"""
The history of the whole group: a line per student, class by class, on Inicio and on its own page. Made-up
students and classes only.
"""
from datetime import date, datetime

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Teacher
from apps.evaluations.models import Evaluation, EvaluationStatus
from apps.materials.models import MaterialStatus, OriginalMaterial, Subject
from apps.students.models import Student

from . import group_progress
from .models import AdaptationStatus, CurricularAdaptation


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class GroupProgressTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.grupo@maestrosombra.test', None, full_name='Docente Grupo',
                                                  accepted_terms=True, terms_accepted_at=timezone.now())
        cls.math = Subject.objects.get(code='MAT')
        cls.first = cls.student('Primer Estudiante Ficticio')
        cls.second = cls.student('Segundo Estudiante Ficticio')
        # Registered in reverse order (week 24 first): the lines and trends follow the weeks
        for day, (week, goal) in enumerate(((24, 'MORE'), (23, 'EXPECTED'), (21, 'LESS')), 1):
            cls.evaluate(cls.first, week, goal, day)
        cls.evaluate(cls.second, 21, 'EXPECTED', 4)

    @classmethod
    def student(cls, name):
        student = Student(teacher=cls.teacher, name=name, birth_date=date(2016, 3, 1), grade='EGB_5',
                          conditions=['DYSCALCULIA'], diagnosis_source='IN_EVALUATION')
        student.save()
        return student

    @classmethod
    def evaluate(cls, student, week, goal, day):
        material = OriginalMaterial.objects.create(
            teacher=cls.teacher, subject=cls.math, title=f'Clase {week}', topic=f'Semana {week} · Áreas',
            input_type='TEXT', input_text='1. Calcula.', status=MaterialStatus.PROCESSED)
        adaptation = CurricularAdaptation.objects.create(
            material=material, student=student, condition='DYSCALCULIA', conditions=['DYSCALCULIA'], version=1,
            title=f'Áreas {week}', status=AdaptationStatus.APPROVED, content={})
        evaluation = Evaluation.objects.create(adaptation=adaptation, status=EvaluationStatus.FINAL,
                                               goal_attainment=goal, autonomy_level='OCCASIONAL_SUPPORT',
                                               comprehension='YES', overload='NONE', achievement_level='NEAR')
        Evaluation.objects.filter(pk=evaluation.pk).update(
            created_at=timezone.make_aware(datetime(2026, 11, day, 10)))

    def setUp(self):
        self.client.force_login(self.teacher)

    def test_one_line_per_student_over_the_shared_classes_in_week_order(self):
        data = group_progress.group_progress(self.teacher)
        goal = data['charts'][0]
        self.assertEqual([c['label'] for c in goal['columns']], ['Sem. 21', 'Sem. 23', 'Sem. 24'])
        self.assertEqual([len(line['points']) for line in goal['lines']], [3, 1])
        self.assertEqual(data['students'][0]['goal']['trend'], 'up')  # −1, 0, +1 by week
        self.assertEqual(data['students'][0]['last_goal'], 'Más de lo esperado (+1)')
        self.assertIsNone(data['students'][1]['goal']['trend'])  # one class: no trend
        # Valid SVG in the Spanish locale: one comma per point, between x and y, never as a decimal mark
        self.assertTrue(all(pair.count(',') == 1 for pair in goal['lines'][0]['polyline'].split()))

    def test_the_general_view_puts_every_subject_together_and_one_subject_can_be_chosen(self):
        language = Subject.objects.get(code='LEN')
        material = OriginalMaterial.objects.create(
            teacher=self.teacher, subject=language, title='Cuento', topic='Semana 23 · El cuento', input_type='TEXT',
            input_text='1. Lee.', status=MaterialStatus.PROCESSED)
        adaptation = CurricularAdaptation.objects.create(
            material=material, student=self.second, condition='DYSCALCULIA', conditions=['DYSCALCULIA'], version=1,
            title='Cuento', status=AdaptationStatus.APPROVED, content={})
        Evaluation.objects.create(adaptation=adaptation, status=EvaluationStatus.FINAL, goal_attainment='MORE',
                                  autonomy_level='AUTONOMOUS', comprehension='YES', overload='NONE',
                                  achievement_level='ACHIEVES')
        general = group_progress.group_progress(self.teacher)
        self.assertTrue(general['general'])
        self.assertIn('Sem. 23 · Len.', [c['label'] for c in general['charts'][0]['columns']])
        self.assertEqual(general['students'][1]['count'], 2)
        self.assertTrue(general['general_note'])
        math = group_progress.group_progress(self.teacher, 'MAT')
        self.assertEqual(math['students'][1]['count'], 1)
        self.assertEqual([c['label'] for c in math['charts'][0]['columns']], ['Sem. 21', 'Sem. 23', 'Sem. 24'])
        page = self.client.get(reverse('group_progress')).content.decode()
        self.assertIn('Todas las asignaturas', page)

    def test_inicio_shows_the_group_chart_and_leads_to_the_full_history(self):
        page = self.client.get(reverse('home')).content.decode()
        self.assertIn('Avance del grupo', page)
        self.assertIn('class="group-chart"', page)
        self.assertIn(reverse('group_progress'), page)
        self.assertIn('Primer Estudiante Ficticio', page)

    def test_the_full_history_has_both_charts_the_summary_and_each_individual_history(self):
        page = self.client.get(reverse('group_progress')).content.decode()
        self.assertEqual(page.count('class="group-chart"'), 2)
        self.assertIn('Resumen por estudiante', page)
        self.assertIn(reverse('student_progress', args=[self.first.pk]), page)
        self.assertIn('no ordena ni compara a los estudiantes', page)

    def test_another_teacher_sees_only_their_own_students(self):
        intruder = Teacher.objects.create_user('otro.grupo@maestrosombra.test', None, full_name='Otro',
                                               accepted_terms=True, terms_accepted_at=timezone.now())
        self.client.force_login(intruder)
        page = self.client.get(reverse('group_progress')).content.decode()
        self.assertNotIn('Primer Estudiante Ficticio', page)
        self.assertIn('Todavía no hay clases evaluadas', page)
