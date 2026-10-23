"""Bank of adaptations, classroom evaluation and AI recommendation. Gemini is replaced by fakes."""
import json
from datetime import date
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Teacher
from apps.adaptations.models import CurricularAdaptation
from apps.adaptations.tests import ADAPTED, ANALYSIS, PASSWORD, fake_adapt
from apps.materials.models import MaterialStatus, OriginalMaterial, Subject
from apps.students.models import Student

from .models import Evaluation, EvaluationStatus, Outcome

ADVICE = ('Reduzca la ficha a dos ejercicios y agregue regletas de fracciones como apoyo visual.', True, 'gemini-test')


def fake_recommend(**kwargs):
    return ADVICE


@mock.patch('apps.evaluations.services.ai.recommend_next_steps', side_effect=fake_recommend)
@mock.patch('apps.adaptations.services.ai.adapt_worksheet', side_effect=fake_adapt)
class EvaluationTests(TestCase):

    def setUp(self):
        self.teacher = Teacher.objects.create_user('ana@correo.com', PASSWORD, full_name='Ana Pérez')
        self.client.force_login(self.teacher)
        self.student = Student.objects.create(teacher=self.teacher, name='Mateo Benítez', birth_date=date(2016, 5, 1),
                                              grade='EGB_4', conditions=['DYSCALCULIA'])
        self.adaptation = self.new_adaptation(Subject.objects.get(code='MAT'), 'Fracciones')

    def new_adaptation(self, subject, title, student=None, teacher=None):
        material = OriginalMaterial.objects.create(
            teacher=teacher or self.teacher, subject=subject, title=title, topic=title, input_type='TEXT',
            input_text='Juan tiene 3/4 de pizza...', status=MaterialStatus.PROCESSED, ai_analysis=dict(ANALYSIS),
        )
        return CurricularAdaptation.objects.create(
            material=material, student=student or self.student, condition='DYSCALCULIA', title=title,
            description='Fracciones con apoyo visual.', content=json.loads(json.dumps(ADAPTED)),
            applied_adjustments=['VISUAL_SUPPORT'],
        )

    def answers(self, **changes):
        data = {'autonomy_level': 'OCCASIONAL_SUPPORT', 'comprehension': 'DIFFICULTY', 'overload': 'MILD',
                'accuracy_range': 'BETWEEN_50_75', 'observation': 'Mateo se confundió con 1/4 y 1/8.',
                'action': 'final'}
        data.update(changes)
        return data

    def test_bank_shows_only_own_newest_versions(self, adapt, recommend):
        CurricularAdaptation.objects.create(
            material=self.adaptation.material, student=self.student, condition='DYSCALCULIA', version=2,
            title='Fracciones v2', content={}, source_adaptation=self.adaptation,
        )
        other = Teacher.objects.create_user('luis@correo.com', PASSWORD, full_name='Luis Mora')
        foreign = Student.objects.create(teacher=other, name='Daniel Vera', birth_date=date(2016, 1, 1),
                                         grade='EGB_4', conditions=['DYSCALCULIA'])
        self.new_adaptation(Subject.objects.get(code='LEN'), 'Lectura ajena', student=foreign, teacher=other)
        page = self.client.get(reverse('bank'))
        self.assertContains(page, 'Fracciones v2')
        self.assertNotContains(page, 'id="card-{}"'.format(self.adaptation.pk))  # old version hidden
        self.assertNotContains(page, 'Lectura ajena')
        self.assertContains(page, 'Pendiente de evaluación')

    def test_bank_filters_by_subject_and_search(self, adapt, recommend):
        self.new_adaptation(Subject.objects.get(code='CIE'), 'El ciclo del agua')
        by_subject = self.client.get(reverse('bank'), {'materia': 'CIE'})
        self.assertContains(by_subject, 'El ciclo del agua')
        self.assertNotContains(by_subject, '>Fracciones<')
        by_text = self.client.get(reverse('bank'), {'q': 'fracc'})
        self.assertContains(by_text, 'Fracciones')
        self.assertNotContains(by_text, 'El ciclo del agua')

    def test_draft_can_be_incomplete_and_skips_the_ai(self, adapt, recommend):
        response = self.client.post(reverse('evaluation', args=[self.adaptation.pk]),
                                    {'overload': 'MILD', 'action': 'draft'})
        self.assertRedirects(response, reverse('evaluation', args=[self.adaptation.pk]))
        evaluation = Evaluation.objects.get()
        self.assertEqual(evaluation.status, EvaluationStatus.DRAFT)
        self.assertEqual(evaluation.outcome, Outcome.PENDING)
        recommend.assert_not_called()

    def test_finishing_requires_autonomy_and_comprehension(self, adapt, recommend):
        response = self.client.post(reverse('evaluation', args=[self.adaptation.pk]),
                                    self.answers(autonomy_level='', comprehension=''))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Responde esta pregunta para finalizar la evaluación.')
        self.assertFalse(Evaluation.objects.exists())

    def test_final_evaluation_saves_the_recommendation(self, adapt, recommend):
        response = self.client.post(reverse('evaluation', args=[self.adaptation.pk]), self.answers())
        self.assertRedirects(response, f"{reverse('bank')}?alumno={self.student.pk}")
        evaluation = Evaluation.objects.get()
        self.assertEqual(evaluation.status, EvaluationStatus.FINAL)
        self.assertEqual(evaluation.ai_recommendation, ADVICE[0])
        self.assertIsNotNone(evaluation.recommended_at)
        self.assertEqual(evaluation.outcome, Outcome.VERIFIED)
        sent = json.dumps(recommend.call_args.kwargs, default=str)
        self.assertNotIn('Mateo', sent)  # the observation is scrubbed
        self.assertContains(self.client.get(reverse('bank')), ADVICE[0])

    def test_regeneration_creates_a_simpler_version(self, adapt, recommend):
        response = self.client.post(reverse('evaluation', args=[self.adaptation.pk]),
                                    self.answers(comprehension='NO', regeneration_requested='on'))
        second = CurricularAdaptation.objects.get(version=2)
        self.assertRedirects(response, f"{reverse('material_detail', args=[self.adaptation.material_id])}?a={second.pk}")
        self.assertEqual(second.source_adaptation, self.adaptation)
        self.assertTrue(adapt.call_args.kwargs['simplify'])
        self.assertNotIn('Mateo', adapt.call_args.kwargs['feedback'])
        self.assertEqual(Evaluation.objects.get().outcome, Outcome.NEEDS_SIMPLER)

    def test_ai_failure_still_saves_the_evaluation(self, adapt, recommend):
        from apps.adaptations import ai
        recommend.side_effect = ai.AIError('La IA no está disponible en este momento.')
        self.client.post(reverse('evaluation', args=[self.adaptation.pk]), self.answers())
        evaluation = Evaluation.objects.get()
        self.assertEqual(evaluation.status, EvaluationStatus.FINAL)
        self.assertEqual(evaluation.ai_recommendation, '')

    def test_observation_is_encrypted(self, adapt, recommend):
        from django.db import connection
        self.client.post(reverse('evaluation', args=[self.adaptation.pk]), self.answers())
        with connection.cursor() as cursor:
            cursor.execute('SELECT observation FROM trx_evaluation')
            self.assertNotIn('confundió', cursor.fetchone()[0])

    def test_other_teacher_cannot_evaluate(self, adapt, recommend):
        intruder = Teacher.objects.create_user('luis@correo.com', PASSWORD, full_name='Luis Mora')
        self.client.force_login(intruder)
        self.assertEqual(self.client.get(reverse('evaluation', args=[self.adaptation.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('evaluation', args=[self.adaptation.pk]), self.answers()).status_code, 404)

    def test_outcome_success(self, adapt, recommend):
        self.client.post(reverse('evaluation', args=[self.adaptation.pk]),
                         self.answers(autonomy_level='AUTONOMOUS', comprehension='YES', overload='NONE',
                                      accuracy_range='ABOVE_75'))
        self.assertEqual(Evaluation.objects.get().outcome, Outcome.SUCCESS)
        self.assertContains(self.client.get(reverse('bank'), {'resultado': 'SUCCESS'}), 'Comprendido con éxito')
