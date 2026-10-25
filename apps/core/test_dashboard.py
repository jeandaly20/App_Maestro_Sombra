"""Main dashboard: real numbers from the teacher's own data."""
import json
from datetime import date

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Teacher
from apps.adaptations.models import AdaptationStatus, CurricularAdaptation
from apps.adaptations.tests import ADAPTED, ANALYSIS, PASSWORD
from apps.evaluations.models import Evaluation, EvaluationStatus
from apps.materials.models import MaterialStatus, OriginalMaterial, Subject
from apps.students.models import Student


@override_settings(MANUAL_ADAPTATION_MINUTES=60)
class DashboardTests(TestCase):

    def setUp(self):
        self.teacher = Teacher.objects.create_user('ana@correo.com', PASSWORD, full_name='Ana Pérez')
        self.client.force_login(self.teacher)
        self.student = Student.objects.create(teacher=self.teacher, name='Mateo Benítez', birth_date=date(2016, 5, 1),
                                              grade='EGB_4', conditions=['DYSCALCULIA'])

    def new_adaptation(self, title, status=AdaptationStatus.IN_REVIEW, teacher=None, student=None):
        material = OriginalMaterial.objects.create(
            teacher=teacher or self.teacher, subject=Subject.objects.get(code='MAT'), title=title, topic=title,
            input_type='TEXT', input_text='Texto', status=MaterialStatus.PROCESSED, ai_analysis=dict(ANALYSIS),
        )
        return CurricularAdaptation.objects.create(
            material=material, student=student or self.student, condition='DYSCALCULIA', title=title,
            content=json.loads(json.dumps(ADAPTED)), status=status, generation_time_sec=30,
        )

    def test_empty_dashboard_invites_to_register(self):
        Student.objects.all().delete()
        page = self.client.get(reverse('home'))
        self.assertContains(page, 'Registrar estudiante')
        self.assertContains(page, 'Aún no hay evaluaciones en el aula.')

    def test_metrics_come_from_own_data(self):
        approved = self.new_adaptation('Fracciones', AdaptationStatus.APPROVED)
        self.new_adaptation('Sumas')
        Evaluation.objects.create(adaptation=approved, status=EvaluationStatus.FINAL,
                                  autonomy_level='AUTONOMOUS', comprehension='DIFFICULTY')
        other = Teacher.objects.create_user('luis@correo.com', PASSWORD, full_name='Luis Mora')
        foreign = Student.objects.create(teacher=other, name='Daniel Vera', birth_date=date(2016, 1, 1),
                                         grade='EGB_4', conditions=['DYSCALCULIA'])
        self.new_adaptation('Ajena', teacher=other, student=foreign)

        context = self.client.get(reverse('home')).context
        self.assertEqual(context['student_count'], 1)
        self.assertEqual(context['adaptation_count'], 2)
        self.assertEqual(context['approved_count'], 1)
        self.assertEqual(context['comprehension'], 50)
        self.assertEqual(context['saved_hours'], 2.0)  # 2 × (60 min − 0.5 min) ≈ 2 h
        self.assertEqual(context['pending_count'], 1)  # "Sumas" still in review
        self.assertNotContains(self.client.get(reverse('home')), 'Ajena')

    def test_pending_filter(self):
        self.new_adaptation('Fracciones', AdaptationStatus.APPROVED)
        Evaluation.objects.create(adaptation=CurricularAdaptation.objects.get(), status=EvaluationStatus.FINAL,
                                  autonomy_level='AUTONOMOUS', comprehension='YES')
        self.new_adaptation('Sumas')
        page = self.client.get(reverse('home'), {'ver': 'pendientes'})
        self.assertContains(page, 'Sumas')
        self.assertNotContains(page, '<strong>Fracciones</strong>', html=False)
