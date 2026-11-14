"""
Task 4.1: privacy of the recommendations made after a classroom evaluation. Gemini is simulated; made-up students.
"""
from datetime import date

from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Teacher
from apps.adaptations import ai, learning_progress
from apps.adaptations.models import CurricularAdaptation
from apps.adaptations.tests import FakeGemini
from apps.materials.models import MaterialStatus, OriginalMaterial, Subject
from apps.students.models import (
    REGISTRATION_CONSENT_VERSION, REGISTRATION_TREATMENTS, ConsentStatus, Student, StudentConsent, Treatment,
)

from . import local_recommendations, services
from .models import Evaluation, EvaluationStatus

NAME = 'Rosa Elvira Cando'
OBS_NAME = 'Rosa Elvira se distrajo cuando llegó su compañero Iván Paredes.'
OBS_DIAGNOSIS = 'Tiene diagnóstico de TDAH y hoy no tomó el metilfenidato antes de clase.'
OBS_FAMILY = 'Su padre está detenido y ahora vive con la abuela; llegó muy triste.'
NOTE = 'Se leyó la consigna en voz alta dos veces.'
PROFILE_TEXT = 'Le cuesta mantener la atención en actividades largas.'
ALL_TREATMENTS = [Treatment.INTERNAL, Treatment.AI_ADAPTATION, Treatment.AI_EVALUATION]


class RecordingGemini(FakeGemini):
    """Records every request; answers the evaluation advice too (or fails with `error`)."""

    def __init__(self, error=None):
        super().__init__()
        self.error = error

    def __call__(self, system, parts, schema):
        if 'recommendation' in schema['properties']:
            self.requests.append({'text': system + ' '.join(str(p.get('text', '')) for p in parts), 'schema': schema})
            if self.error:
                raise self.error
            return {'recommendation': 'Recomendación de prueba generada por la IA.', 'simplify': False}, 'fake'
        return super().__call__(system, parts, schema)


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False, AI_EVALUATION_TRANSFER_ENABLED=True)
class EvaluationPrivacyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.evaluacion@maestrosombra.test', None,
                                                  full_name='Docente de Prueba', accepted_terms=True,
                                                  terms_accepted_at=timezone.now())
        cls.student = Student(teacher=cls.teacher, name=NAME, birth_date=date(2016, 7, 14), grade='EGB_5',
                              conditions=['ADHD'], diagnosis_source='IN_EVALUATION', learning_barriers=PROFILE_TEXT)
        cls.student.save()
        cls.language = Subject.objects.get(code='LEN')

    # ---------- helpers ----------

    def worksheet(self, topic='El sustantivo', student=None):
        material = OriginalMaterial.objects.create(
            teacher=self.teacher, subject=self.language, title=topic, topic=topic, input_type='TEXT',
            input_text='1. Lee. 2. Subraya. 3. Escribe.', status=MaterialStatus.PROCESSED)
        return CurricularAdaptation.objects.create(
            material=material, student=student or self.student, condition='ADHD', conditions=['ADHD'],
            title=f'{topic} adaptada', description='Ficha con instrucciones breves.',
            applied_adjustments=['SEGMENTATION'], content={'learning_goal': 'Vas a aprender a reconocer sustantivos.'})

    def evaluation(self, adaptation=None, **values):
        fields = {'status': EvaluationStatus.FINAL, 'goal_attainment': 'LESS', 'autonomy_level': 'FREQUENT_SUPPORT',
                  'comprehension': 'DIFFICULTY', 'overload': 'MILD', **values}
        return Evaluation.objects.create(adaptation=adaptation or self.worksheet(), **fields)

    def consent(self, treatments=ALL_TREATMENTS, student=None, **extra):
        fields = {'document_version': 'IA-EVAL-PRUEBA', 'authorized_on': date(2026, 11, 1), **extra}
        return StudentConsent.objects.create(student=student or self.student, treatments=list(treatments), **fields)

    def recommend(self, evaluation, error=None):
        fake = RecordingGemini(error)
        with mock.patch.object(ai, 'generate_json', fake):
            services.recommend(evaluation)
        evaluation.refresh_from_db()
        return fake

    def finish(self, adaptation, error=None, **data):
        self.client.force_login(self.teacher)
        fake = RecordingGemini(error)
        post = {'action': 'final', 'goal_attainment': 'LESS', 'autonomy_level': 'FREQUENT_SUPPORT',
                'comprehension': 'DIFFICULTY', 'overload': 'MILD', **data}
        with mock.patch.object(ai, 'generate_json', fake):
            response = self.client.post(reverse('evaluation', args=[adaptation.pk]), post)
        return response, fake

    def adapted(self):
        """A worksheet made by the (simulated) AI, so it can be regenerated."""
        material = OriginalMaterial.objects.create(teacher=self.teacher, subject=self.language, title='Sustantivos',
                                                   topic='Sustantivos del cuento', input_type='TEXT',
                                                   input_text='1. Subraya. 2. Escribe. 3. Lee. 4. Clasifica. 5. Explica.')
        from apps.adaptations import services as adaptation_services
        with mock.patch.object(ai, 'generate_json', RecordingGemini()):
            adaptation_services.process_material(material.pk, [(self.student.pk, ['ADHD'])], {self.student.pk: []})
        return material.adaptations.get()

    # ---------- 1-5: the authorization ----------

    def test_01_without_ai_authorization(self):
        evaluation = self.evaluation(observation=OBS_NAME)
        fake = self.recommend(evaluation)
        self.assertEqual(fake.requests, [])
        self.assertEqual(evaluation.recommendation_source, 'LOCAL')
        self.assertEqual(evaluation.recommendation_note, services.REASONS[services.NO_CONSENT])
        self.assertEqual(evaluation.ai_recommendation, '')
        self.assertTrue(evaluation.local_recommendations)

    def test_02_with_a_specific_authorization_in_force(self):
        self.consent()
        evaluation = self.evaluation()
        fake = self.recommend(evaluation)
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual((evaluation.recommendation_source, evaluation.recommendation_note), ('AI', ''))
        self.assertEqual(evaluation.ai_recommendation, 'Recomendación de prueba generada por la IA.')
        self.assertIn('Nivel de apoyo: Apoyo frecuente', fake.everything())
        self.assertTrue(evaluation.local_recommendations)  # the local ones are prepared as well

    def test_03_revoked_authorization(self):
        self.consent(status=ConsentStatus.REVOKED, revoked_at=timezone.now())
        evaluation = self.evaluation()
        self.assertEqual(self.recommend(evaluation).requests, [])
        self.assertEqual(evaluation.recommendation_source, 'LOCAL')

    def test_04_another_students_authorization(self):
        other = Student(teacher=self.teacher, name='Otro Estudiante', birth_date=date(2016, 1, 1), grade='EGB_5',
                        conditions=['ADHD'], diagnosis_source='IN_EVALUATION')
        other.save()
        self.consent(student=other)
        evaluation = self.evaluation()
        self.assertEqual(self.recommend(evaluation).requests, [])
        self.assertEqual(evaluation.recommendation_note, services.REASONS[services.NO_CONSENT])

    def test_05_an_old_authorization_does_not_cover_it(self):
        self.consent(list(REGISTRATION_TREATMENTS) + [Treatment.AI_PROFILE], document_version=REGISTRATION_CONSENT_VERSION)
        evaluation = self.evaluation()
        self.assertEqual(self.recommend(evaluation).requests, [])
        self.assertEqual(evaluation.recommendation_source, 'LOCAL')
        # And a valid one is not enough while the system setting is off (its default)
        self.consent()
        with self.settings(AI_EVALUATION_TRANSFER_ENABLED=False):
            evaluation = self.evaluation()
            self.assertEqual(self.recommend(evaluation).requests, [])
        self.assertEqual(evaluation.recommendation_note, services.REASONS[services.DISABLED])

    # ---------- 6-11: what the AI may receive ----------

    def test_06_observation_with_a_personal_name(self):
        self.consent()
        sent = self.recommend(self.evaluation(observation=OBS_NAME)).everything()
        for private in ('Rosa', 'Iván Paredes', 'se distrajo', NAME):
            self.assertNotIn(private, sent)

    def test_07_observation_with_a_diagnosis(self):
        self.consent()
        sent = self.recommend(self.evaluation(observation=OBS_DIAGNOSIS)).everything()
        self.assertNotIn('metilfenidato', sent)
        self.assertNotIn('diagnóstico de TDAH', sent)

    def test_08_observation_with_family_information(self):
        self.consent()
        sent = self.recommend(self.evaluation(observation=OBS_FAMILY)).everything()
        for private in ('padre', 'detenido', 'abuela', 'triste'):
            self.assertNotIn(private, sent)

    def test_09_empty_observation(self):
        self.consent()
        evaluation = self.evaluation(observation='')
        self.assertEqual(len(self.recommend(evaluation).requests), 1)
        self.assertEqual(evaluation.recommendation_source, 'AI')

    def test_10_the_learning_history_is_left_out(self):
        self.consent()
        past = self.evaluation(self.worksheet('Tema anterior Omega'), goal_attainment='MUCH_LESS', comprehension='NO')
        sent = self.recommend(self.evaluation()).everything()
        for private in ('Tema anterior Omega', past.adaptation.code, 'Mucho menos de lo esperado', 'No comprendió',
                        'Historial', 'historial', 'Antecedentes'):
            self.assertNotIn(private, sent)

    def test_11_only_the_minimal_indicators(self):
        self.consent()
        evaluation = self.evaluation(observation=OBS_DIAGNOSIS, supports_applied=['pausas', 'lectura_acompanada'],
                                     supports_useful=['pausas'], supports_note=NOTE)
        context = services.minimal_context(evaluation)
        self.assertEqual(set(context), {'subject', 'topic', 'grade', 'conditions', 'adjustments', 'summary', 'evaluation'})
        sent = self.recommend(evaluation).everything()
        for expected in ('Lengua y Literatura', 'El sustantivo', 'Logro del objetivo adaptado: Menos de lo esperado (−1)',
                         'Apoyos aplicados durante la actividad: Pausas programadas, Lectura acompañada',
                         'Apoyos que el docente consideró útiles: Pausas programadas'):
            self.assertIn(expected, sent)
        for private in (NAME, '14/07/2016', '2016-07-14', NOTE, 'voz alta', PROFILE_TEXT, 'IA-EVAL-PRUEBA',
                        self.student.pseudonym, 'metilfenidato'):
            self.assertNotIn(private, sent)

    # ---------- 12-15: local recommendations, their origin and saving without the AI ----------

    @override_settings(AI_EVALUATION_TRANSFER_ENABLED=False)
    def test_12_local_recommendations_without_gemini(self):
        evaluation = self.evaluation(supports_applied=['pausas'], supports_useful=['pausas'])
        with mock.patch.object(ai, 'generate_json', side_effect=AssertionError('Gemini must not be called')):
            services.recommend(evaluation)
        evaluation.refresh_from_db()
        texts = [item['text'] for item in evaluation.local_recommendations]
        self.assertTrue(texts[0].startswith('Conviene revisar la claridad de las instrucciones y comprobar la comprensión'))
        self.assertIn('«Vas a aprender a reconocer sustantivos»', texts[0])  # the activity's objective
        self.assertIn('El docente puede valorar una organización de la actividad en segmentos más breves.', texts)
        self.assertIn('Se registró «Pausas programadas» como útil durante la actividad. Conviene valorar su pertinencia '
                      'en futuras actividades similares.', texts)
        joined = ' '.join(texts).lower()
        for claim in ('demuestra', 'gracias a', 'causó', 'diagnóstico', 'tdah'):
            self.assertNotIn(claim, joined)

    def test_13_local_recommendation_with_incomplete_indicators(self):
        evaluation = Evaluation(adaptation=self.worksheet(), status=EvaluationStatus.FINAL, comprehension='YES',
                                autonomy_level='OCCASIONAL_SUPPORT')
        items = local_recommendations.recommend(evaluation)
        self.assertEqual([item['text'] for item in items], [local_recommendations.INSUFFICIENT])
        self.assertNotIn('Logro', items[0]['basis'])  # an answer that was not given is not made up
        self.assertNotIn('Sobrecarga', items[0]['basis'])

    def test_14_the_origin_of_each_recommendation_is_shown(self):
        adaptation = self.worksheet()
        evaluation = self.evaluation(adaptation)
        self.recommend(evaluation)
        self.client.force_login(self.teacher)
        page = self.client.get(reverse('evaluation', args=[adaptation.pk])).content.decode()
        self.assertIn('Generadas con reglas locales', page)
        self.assertNotIn('Generada por IA', page)
        self.assertIn('Pendiente de revisión', page)
        self.client.post(reverse('evaluation_recommendation_review', args=[adaptation.pk]))
        self.assertIn('Revisada el', self.client.get(reverse('evaluation', args=[adaptation.pk])).content.decode())
        self.consent()
        other = self.worksheet('Otro tema')
        self.recommend(self.evaluation(other))
        page = self.client.get(reverse('evaluation', args=[other.pk])).content.decode()
        self.assertIn('Generada por IA', page)
        self.assertIn('Generadas con reglas locales', page)

    def test_15_the_evaluation_is_saved_without_any_external_connection(self):
        self.consent()
        adaptation = self.worksheet()
        response, fake = self.finish(adaptation, error=ai.AIError('Sin conexión'))
        self.assertEqual(response.status_code, 302)
        evaluation = Evaluation.objects.get(adaptation=adaptation)
        self.assertEqual(evaluation.status, EvaluationStatus.FINAL)
        self.assertEqual((evaluation.recommendation_source, evaluation.recommendation_note),
                         ('LOCAL', services.REASONS[services.AI_ERROR]))
        self.assertEqual(len(fake.requests), 1)

    # ---------- 16-18: regeneration and errors ----------

    def test_16_regeneration_without_unauthorized_observations(self):
        adaptation = self.adapted()
        response, fake = self.finish(adaptation, observation=OBS_FAMILY, regeneration_requested='on',
                                     supports_note=NOTE)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(adaptation.material.adaptations.count(), 2)  # the new version was made
        sent = fake.everything()
        for private in ('abuela', 'padre', 'voz alta', 'Indicadores registrados en el aula', 'Apoyo frecuente'):
            self.assertNotIn(private, sent)
        # The bank's button no longer carries the observation, and free text posted to it is ignored
        self.client.force_login(self.teacher)
        self.assertNotIn('abuela', self.client.get(reverse('bank')).content.decode())
        other = RecordingGemini()
        newest = adaptation.material.adaptations.order_by('-version').first()
        with mock.patch.object(ai, 'generate_json', other):
            self.client.post(reverse('adaptation_action', args=[newest.pk]),
                             {'action': 'regenerate', 'feedback': OBS_DIAGNOSIS})
        self.assertNotIn('metilfenidato', other.everything())

    def test_17_regeneration_with_authorized_curricular_context(self):
        self.consent()
        adaptation = self.adapted()
        response, fake = self.finish(adaptation, observation=OBS_DIAGNOSIS, regeneration_requested='on')
        self.assertEqual(response.status_code, 302)
        sent = fake.everything()
        self.assertIn('Indicadores registrados en el aula', sent)
        self.assertIn('Nivel de apoyo: Apoyo frecuente', sent)
        self.assertIn('REGENERACIÓN', sent)  # simpler version of the same worksheet, objectives kept
        self.assertNotIn('metilfenidato', sent)

    def test_18_errors_of_the_gemini_service(self):
        self.consent()
        for error in (ai.AIError('Saturado'), RuntimeError('fallo inesperado'), TimeoutError()):
            evaluation = self.evaluation()
            self.recommend(evaluation, error=error)  # never raises
            self.assertEqual(evaluation.recommendation_source, 'LOCAL')
            self.assertEqual(evaluation.recommendation_note, services.REASONS[services.AI_ERROR])
            self.assertTrue(evaluation.local_recommendations)

    # ---------- 19-22: records, access, logs and the history ----------

    def test_19_previous_evaluations_are_kept(self):
        before = timezone.now()
        evaluation = self.evaluation(observation=OBS_NAME, ai_recommendation='Recomendación anterior de la IA.',
                                     recommended_at=before)
        self.assertEqual((evaluation.recommendation_source, evaluation.local_recommendations), ('', []))
        self.recommend(evaluation)  # now without authorization
        self.assertEqual(evaluation.ai_recommendation, 'Recomendación anterior de la IA.')  # not modified nor deleted
        self.assertEqual(evaluation.recommended_at, before)
        self.assertEqual((evaluation.goal_attainment, evaluation.comprehension, evaluation.observation),
                         ('LESS', 'DIFFICULTY', OBS_NAME))
        self.client.force_login(self.teacher)
        page = self.client.get(reverse('evaluation', args=[evaluation.adaptation.pk])).content.decode()
        self.assertIn('recomendación anterior', page)

    def test_20_only_the_students_teacher(self):
        adaptation = self.worksheet()
        evaluation = self.evaluation(adaptation)
        intruder = Teacher.objects.create_user('intruso.eval@maestrosombra.test', None, full_name='Intruso',
                                               accepted_terms=True, terms_accepted_at=timezone.now())
        self.client.force_login(intruder)
        self.assertEqual(self.client.get(reverse('evaluation', args=[adaptation.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('evaluation_recommendation_review', args=[adaptation.pk])).status_code, 404)
        evaluation.refresh_from_db()
        self.assertIsNone(evaluation.recommendation_reviewed_at)

    def test_21_no_sensitive_data_in_the_logs(self):
        self.consent()
        adaptation = self.worksheet()
        with self.assertLogs('maestro_sombra', level='INFO') as logs:
            self.finish(adaptation, error=ai.AIError('Saturado'), observation=OBS_DIAGNOSIS, supports_note=NOTE)
        output = '\n'.join(logs.output)
        self.assertIn('source=LOCAL', output)
        for private in (NAME, 'Rosa', 'metilfenidato', 'voz alta', PROFILE_TEXT, 'Saturado'):
            self.assertNotIn(private, output)

    def test_22_works_with_the_learning_history(self):
        adaptation = self.worksheet()
        self.finish(adaptation, supports_applied=['pausas'], supports_useful=['pausas'])
        entries = learning_progress.history(self.student)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].supports_applied, ['Pausas programadas'])
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse('student_progress', args=[self.student.pk])).status_code, 200)
