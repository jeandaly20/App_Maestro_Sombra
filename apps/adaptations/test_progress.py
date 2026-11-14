"""
Task 4: the learning history and the progressive adaptation (learning_progress). Made-up students and
activities; Gemini simulated (no external calls).
"""
from datetime import date, datetime
from unittest import mock

from django.core.files.base import ContentFile
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Teacher
from apps.evaluations.models import (
    SUPPORT_KEYS, Evaluation, EvaluationStatus, Outcome, RecommendationDecision, RecommendationReview,
)
from apps.evaluations.forms import EvaluationForm
from apps.materials.models import MaterialStatus, OriginalMaterial, Subject
from apps.students.models import Student, StudentConsent, Treatment

from . import ai, learning_progress as progress, pedagogical_relevance, services
from .models import CurricularAdaptation, LessonAdaptation, LessonPlan
from .tests import FakeGemini

SECRET = 'Observación privada: se cansó al final y pidió ir a casa.'
NOTE = 'Se leyó la consigna en voz alta.'
DIAGNOSES = sorted({w for words in pedagogical_relevance.DIAGNOSIS_WORDS.values() for w in words}
                   | set(pedagogical_relevance.OTHER_DIAGNOSES))


def day(n):
    return timezone.make_aware(datetime(2026, 10, n, 10, 0))


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class LearningProgressTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.seguimiento@maestrosombra.test', None,
                                                  full_name='Docente de Prueba', accepted_terms=True,
                                                  terms_accepted_at=timezone.now())
        cls.student = Student(teacher=cls.teacher, name='Estudiante Ficticio', birth_date=date(2016, 3, 1),
                              grade='EGB_5', conditions=['DYSLEXIA'], diagnosis_source='IN_EVALUATION')
        cls.student.save()
        cls.language, cls.math = Subject.objects.get(code='LEN'), Subject.objects.get(code='MAT')

    # ---------- helpers ----------

    def worksheet(self, subject=None, topic='El sustantivo', *, material=None, version=1, source=None, content=None,
                  adjustments=(), student=None):
        material = material or OriginalMaterial.objects.create(
            teacher=self.teacher, subject=subject or self.language, title=f'Ficha {topic}', topic=topic,
            input_type='TEXT', input_text='1. Lee. 2. Subraya. 3. Escribe.', status=MaterialStatus.PROCESSED)
        adaptation = CurricularAdaptation(
            material=material, student=student or self.student, condition='DYSLEXIA', conditions=['DYSLEXIA'],
            version=version, source_adaptation=source, title=f'Ficha adaptada: {topic}',
            content=content or {'learning_goal': f'Vas a aprender {topic.lower()}.'}, applied_adjustments=list(adjustments))
        adaptation.save()
        return adaptation

    def evaluate(self, adaptation, on, status=EvaluationStatus.FINAL, **values):
        fields = {'autonomy_level': 'OCCASIONAL_SUPPORT', 'comprehension': 'YES', **values}
        evaluation = Evaluation.objects.create(adaptation=adaptation, status=status, **fields)
        Evaluation.objects.filter(pk=evaluation.pk).update(created_at=day(on))
        return evaluation

    def lesson_worksheet(self, skill, topic):
        adaptation = self.worksheet(self.math, topic)
        lesson = LessonPlan.objects.create(teacher=self.teacher, subject=self.math, material=adaptation.material,
                                           plan_file=ContentFile(b'plan', name='plan.xlsx'), title=topic)
        LessonAdaptation.objects.create(lesson=lesson, student=self.student, condition='DYSLEXIA',
                                        conditions=['DYSLEXIA'], grade='NON_SIGNIFICANT', worksheet=adaptation,
                                        content={'relevance': {'skill': skill, 'objective': f'Objetivo de {topic}'},
                                                 'adapted_objective': 'Objetivo adaptado'})
        return adaptation

    def page(self, query=''):
        self.client.force_login(self.teacher)
        return self.client.get(reverse('student_progress', args=[self.student.pk]) + query)

    def tearDown(self):
        from django.core.files.storage import default_storage
        for lesson in LessonPlan.objects.filter(teacher=self.teacher):
            if lesson.plan_file:
                default_storage.delete(lesson.plan_file.name)

    # ---------- 1-3: number of evaluations ----------

    def test_01_student_without_evaluations(self):
        data = progress.progress(self.student)
        self.assertEqual((data['total'], data['entries'], data['recommendations'], data['groups']), (0, [], [], []))
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Todavía no hay actividades evaluadas')
        self.assertContains(response, progress.INSUFFICIENT)

    def test_02_student_with_one_evaluation(self):
        self.evaluate(self.worksheet(), 1, goal_attainment='EXPECTED')
        data = progress.progress(self.student)
        self.assertEqual(data['total'], 1)
        group = data['groups'][0]
        self.assertFalse(group['comparable'])
        self.assertEqual(group['series'], [])
        self.assertIn('Una sola actividad', group['message'])
        self.assertIn('No existen evaluaciones suficientes en Lengua', data['recommendations'][0]['text'])

    def test_03_student_with_several_evaluations(self):
        for n, topic in enumerate(('El sustantivo', 'El verbo', 'El adjetivo'), start=1):
            self.evaluate(self.worksheet(topic=topic), n)
        self.evaluate(self.worksheet(self.math, 'Fracciones'), 4)
        data = progress.progress(self.student)
        self.assertEqual(data['total'], 4)
        self.assertEqual(dict(data['subjects']), {'LEN': 'Lengua y Literatura', 'MAT': 'Matemática'})
        self.assertEqual([item['count'] for item in data['by_subject']], [3, 1])

    # ---------- 4-8: order, filters and comparability ----------

    def test_04_history_in_chronological_order(self):
        self.evaluate(self.worksheet(topic='Tercera'), 20)
        self.evaluate(self.worksheet(topic='Primera'), 2)
        self.evaluate(self.worksheet(topic='Segunda'), 11)
        self.assertEqual([e.topic for e in progress.history(self.student)], ['Primera', 'Segunda', 'Tercera'])
        html = self.page().content.decode()
        self.assertLess(html.index('Primera'), html.index('Segunda'))
        self.assertLess(html.index('Segunda'), html.index('Tercera'))

    def test_05_filter_by_subject(self):
        self.evaluate(self.worksheet(topic='Sustantivos del cuento'), 1)
        self.evaluate(self.worksheet(self.math, 'Repartos iguales'), 2)
        data = progress.progress(self.student, 'MAT')
        self.assertEqual([e.subject_code for e in data['entries']], ['MAT'])
        html = self.page('?materia=MAT').content.decode()
        self.assertIn('Repartos iguales', html)
        self.assertNotIn('Sustantivos del cuento', html)
        everything = self.page('?materia=XYZ').content.decode()  # an unknown subject shows everything
        self.assertIn('Sustantivos del cuento', everything)

    def test_06_different_objectives_are_not_compared(self):
        self.evaluate(self.lesson_worksheet('M.2.1.1. Contar colecciones.', 'Conteo'), 1, goal_attainment='LESS')
        self.evaluate(self.lesson_worksheet('M.2.1.5. Reconocer fracciones.', 'Fracciones'), 2, goal_attainment='EXPECTED')
        self.evaluate(self.lesson_worksheet('M.2.2.3. Medir longitudes.', 'Medidas'), 3, goal_attainment='MORE')
        groups = progress.progress(self.student)['groups']
        self.assertEqual(len(groups), 3)
        self.assertTrue(all(not g['comparable'] and not g['series'] for g in groups))
        entry = progress.history(self.student)[0]
        self.assertEqual((entry.skill, entry.objective, entry.objective_source),
                         ('M.2.1.1. Contar colecciones.', 'Objetivo de Conteo', 'Objetivo de la clase'))

    def test_07_comparable_evaluations(self):
        # The same skill (its code), written differently in each class plan
        self.evaluate(self.lesson_worksheet('M.2.1.5. Reconocer fracciones simples.', 'Mitades'), 1, goal_attainment='LESS')
        self.evaluate(self.lesson_worksheet('M.2.1.5 reconocer fracciones', 'Cuartos'), 8, goal_attainment='EXPECTED')
        self.evaluate(self.lesson_worksheet('Destreza M.2.1.5.', 'Octavos'), 15, goal_attainment='MORE')
        groups = progress.progress(self.student)['groups']
        self.assertEqual(len(groups), 1)
        goal = next(s for s in groups[0]['series'] if s['indicator'] == 'goal_attainment')
        self.assertTrue(groups[0]['comparable'])
        self.assertEqual(goal['trend'], 'up')
        self.assertIn('de «Menos de lo esperado (−1)» a «Más de lo esperado (+1)», 3 evaluaciones', goal['text'])
        self.assertNotIn('mejor', goal['text'].lower())  # observed evolution, never a demonstrated improvement

    def test_08_insufficient_data_for_a_trend(self):
        self.evaluate(self.worksheet(topic='Sílabas'), 1, goal_attainment='LESS')
        self.evaluate(self.worksheet(topic='Sílabas'), 5, goal_attainment='MORE')
        group = progress.progress(self.student)['groups'][0]
        self.assertEqual(group['count'], 2)
        self.assertEqual((group['comparable'], group['series'], group['message']), (False, [], progress.INSUFFICIENT))
        series = progress.describe_series(group['entries'], 'goal_attainment')
        self.assertIsNone(series['trend'])
        self.assertContains(self.page(), progress.INSUFFICIENT)

    # ---------- 9-10: supports ----------

    def test_09_applied_supports_are_recorded(self):
        adaptation = self.worksheet()
        self.client.force_login(self.teacher)
        with mock.patch('apps.evaluations.views.services.recommend', side_effect=lambda evaluation: evaluation):
            response = self.client.post(reverse('evaluation', args=[adaptation.pk]), {
                'action': 'final', 'goal_attainment': 'EXPECTED', 'autonomy_level': 'OCCASIONAL_SUPPORT',
                'comprehension': 'YES', 'supports_applied': ['pausas', 'lectura_acompanada'],
                'supports_useful': ['pausas'], 'supports_note': NOTE})
        self.assertEqual(response.status_code, 302)
        evaluation = Evaluation.objects.get(adaptation=adaptation)
        self.assertEqual((evaluation.supports_applied, evaluation.supports_useful, evaluation.supports_note),
                         (['pausas', 'lectura_acompanada'], ['pausas'], NOTE))
        with connection.cursor() as cursor:
            cursor.execute('SELECT supports_note FROM trx_evaluation WHERE id = %s', [evaluation.pk])
            self.assertNotIn('voz alta', cursor.fetchone()[0])  # stored encrypted
        applied = {item['label']: item for item in progress.supports_summary(progress.history(self.student))['applied']}
        self.assertEqual((applied['Pausas programadas']['count'], applied['Pausas programadas']['useful']), (1, 1))
        self.assertEqual(applied['Lectura acompañada']['useful'], 0)

    def test_10_suggested_supports_are_not_applied_supports(self):
        content = {'relevance': {'relevant_supports': [{'category': 'pausas', 'label': 'pausas programadas'}],
                                 'udl_suggestions': [{'label': 'Lectura fácil', 'pertinent': True}]}}
        self.evaluate(self.worksheet(content=content, adjustments=['SEGMENTATION']), 1)
        summary = progress.supports_summary(progress.history(self.student))
        self.assertEqual([s['label'] for s in summary['suggested']], ['pausas programadas', 'Lectura fácil'])
        self.assertEqual([s['label'] for s in summary['in_material']], ['Segmentación de enunciados'])
        self.assertEqual((summary['applied'], summary['not_recorded']), ([], 1))
        self.assertFalse(any('apoyo:' in r['key'] for r in progress.progress(self.student)['recommendations']))
        # Useful only if applied: the form and the database both refuse it
        form = EvaluationForm({'goal_attainment': 'EXPECTED', 'autonomy_level': 'AUTONOMOUS', 'comprehension': 'YES',
                               'supports_useful': ['pausas']}, instance=Evaluation(adaptation=self.worksheet(topic='Otra')))
        self.assertFalse(form.is_valid())
        self.assertIn('supports_useful', form.errors)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Evaluation.objects.create(adaptation=self.worksheet(topic='Otra más'), supports_useful=['pausas'])
        self.assertEqual(sorted(SUPPORT_KEYS), sorted(pedagogical_relevance.SUPPORTS))  # the same categories

    # ---------- 11-12: indicators and no invented data ----------

    def test_11_indicators_are_counted_correctly(self):
        values = [('LESS', 'DIFFICULTY', 'FREQUENT_SUPPORT', 'EVIDENT'), ('EXPECTED', 'YES', 'OCCASIONAL_SUPPORT', 'MILD'),
                  ('EXPECTED', 'YES', 'AUTONOMOUS', 'NONE'), ('MORE', 'NO', 'OCCASIONAL_SUPPORT', '')]
        for n, (goal, comprehension, autonomy, overload) in enumerate(values, start=1):
            self.evaluate(self.worksheet(topic=f'Tema {n}'), n, goal_attainment=goal, comprehension=comprehension,
                          autonomy_level=autonomy, overload=overload)
        data = progress.progress(self.student)
        goal = {row['value']: row['count'] for row in data['by_subject'][0]['goal']['rows']}
        self.assertEqual((goal['LESS'], goal['EXPECTED'], goal['MORE'], goal['MUCH_MORE']), (1, 2, 1, 0))
        self.assertEqual({row['value']: row['count'] for row in data['comprehension']['rows']},
                         {'YES': 2, 'DIFFICULTY': 1, 'NO': 1})
        self.assertEqual(data['overload'], {'registered': 3, 'with_signs': 2, 'evident': 1, 'missing': 1})
        self.assertEqual(data['autonomy']['total'], 4)

    def test_12_no_invented_data(self):
        self.evaluate(self.worksheet(topic='Con logro'), 1, goal_attainment='EXPECTED')
        self.evaluate(self.worksheet(topic='Sin logro'), 3)  # an old evaluation without the goal question
        entries = progress.history(self.student)
        chart = progress.chart(entries)
        self.assertEqual(len(chart['points']), 1)
        self.assertNotIn(',', chart['points'][0]['x'] + chart['points'][0]['y'])  # valid SVG in the Spanish locale
        self.assertEqual([e.topic for e in chart['not_plotted']], ['Sin logro'])
        self.assertEqual(entries[1].labels['goal_attainment'], '')
        self.assertEqual(progress.distribution(entries, 'goal_attainment')['missing'], 1)
        self.assertEqual([e.date.day for e in entries], [1, 3])  # the real dates, none filled in
        self.assertTrue(any('Logro del objetivo: sin registrar en 1 de 2' in a
                            for a in progress.progress(self.student)['insufficient']))

    # ---------- 13-14: access and observations ----------

    def test_13_only_the_students_teacher_can_see_it(self):
        self.evaluate(self.worksheet(), 1)
        url = reverse('student_progress', args=[self.student.pk])
        self.assertEqual(self.client.get(url).status_code, 302)  # not signed in: to the login page
        intruder = Teacher.objects.create_user('otro.docente@maestrosombra.test', None, full_name='Otro Docente',
                                               accepted_terms=True, terms_accepted_at=timezone.now())
        self.client.force_login(intruder)
        self.assertEqual(self.client.get(url).status_code, 404)
        key = progress.progress(self.student)['recommendations'][0]['key']
        review = reverse('student_recommendation_review', args=[self.student.pk])
        self.assertEqual(self.client.post(review, {'key': key, 'decision': 'CONSIDERED'}).status_code, 404)
        self.assertFalse(RecommendationReview.objects.exists())
        self.assertEqual(self.page().status_code, 200)

    def test_14_encrypted_observations_stay_protected(self):
        adaptation = self.worksheet()
        self.evaluate(adaptation, 1, observation=SECRET, supports_note=NOTE)
        html = self.page().content.decode()
        self.assertNotIn('se cansó', html)
        self.assertNotIn('voz alta', html)
        self.assertIn('Observación cifrada (solo en la evaluación)', html)
        self.assertNotIn('se cansó', str(progress.adaptation_context(self.student, 'LEN')))
        with connection.cursor() as cursor:
            cursor.execute('SELECT observation FROM trx_evaluation WHERE adaptation_id = %s', [adaptation.pk])
            self.assertNotIn('se cansó', cursor.fetchone()[0])

    # ---------- 15-16: recommendations ----------

    def test_15_recommendations_carry_their_evidence(self):
        first = self.worksheet(topic='Comprensión lectora')
        self.evaluate(first, 1, comprehension='DIFFICULTY', goal_attainment='LESS')
        self.evaluate(self.worksheet(topic='Comprensión lectora'), 6, comprehension='NO')
        self.evaluate(self.worksheet(topic='Comprensión lectora'), 9, comprehension='YES')
        rec = next(r for r in progress.progress(self.student)['recommendations'] if r['key'].startswith('dificultad:'))
        self.assertIn('Conviene revisar las actividades del tema «Comprensión lectora»', rec['text'])
        self.assertIn('En 2 de 3 actividades', rec['reason'])
        self.assertEqual(len(rec['activities']), 2)
        self.assertIn(first.code, rec['evidence'])
        self.assertTrue(rec['limitations'] and rec['pending'])
        # The teacher reviews it; new evidence makes it pending again
        self.client.force_login(self.teacher)
        self.client.post(reverse('student_recommendation_review', args=[self.student.pk]),
                         {'key': rec['key'], 'decision': RecommendationDecision.CONSIDERED})
        again = next(r for r in progress.progress(self.student)['recommendations'] if r['key'] == rec['key'])
        self.assertFalse(again['pending'])
        self.evaluate(self.worksheet(topic='Comprensión lectora'), 12, comprehension='DIFFICULTY')
        later = next(r for r in progress.progress(self.student)['recommendations'] if r['key'] == rec['key'])
        self.assertTrue(later['pending'])
        self.assertEqual(self.student.learning_strengths, '')  # reviewing never touches the profile

    def test_16_no_invented_diagnoses(self):
        for n in range(1, 5):
            self.evaluate(self.worksheet(topic='Lectura de palabras'), n, comprehension='NO', goal_attainment='MUCH_LESS',
                          overload='EVIDENT', supports_applied=['pausas'], supports_useful=['pausas'])
        data = progress.progress(self.student)
        texts = ' '.join(f"{r['text']} {r['reason']} {r['limitations']}" for r in data['recommendations']).lower()
        texts += ' '.join(s['text'] for g in data['groups'] for s in g['series']).lower()
        for word in DIAGNOSES:
            self.assertFalse(pedagogical_relevance.mentions(progress.normalized(texts), progress.normalized(word)), word)
        for claim in ('mejora demostrada', 'gracias a', 'debido a', 'causó', 'riesgo'):
            self.assertNotIn(claim, texts)
        support = next(r for r in data['recommendations'] if r['key'].startswith('apoyo:'))
        self.assertIn('no demuestra que haya causado', support['limitations'])

    # ---------- 17-18: bank and regenerations ----------

    def test_17_bank_still_works(self):
        adaptation = self.worksheet()
        evaluation = self.evaluate(adaptation, 1, supports_applied=['pausas'], comprehension='YES',
                                   autonomy_level='AUTONOMOUS')
        self.assertEqual(evaluation.outcome, Outcome.SUCCESS)  # the bank badge rules did not change
        self.client.force_login(self.teacher)
        response = self.client.get(reverse('bank') + f'?alumno={self.student.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, adaptation.code)
        self.assertEqual(self.client.get(reverse('evaluation', args=[adaptation.pk])).status_code, 200)

    def test_18_regenerations_are_told_apart(self):
        first = self.worksheet(topic='Fracciones', subject=self.math)
        self.evaluate(first, 1, comprehension='NO', regeneration_requested=True)
        second = self.worksheet(material=first.material, version=2, source=first)
        recs = progress.progress(self.student)['recommendations']
        self.assertTrue(any(r['key'].startswith('regeneracion:') for r in recs))
        self.evaluate(second, 4, comprehension='YES')
        entries = progress.history(self.student)
        self.assertEqual([(e.version, e.is_regeneration, e.source_code) for e in entries],
                         [(1, False, ''), (2, True, first.code)])
        self.assertFalse(any(r['key'].startswith('regeneracion:') for r in progress.progress(self.student)['recommendations']))
        self.assertContains(self.page(), f'Regeneración de {first.code}')

    # ---------- 19-20: the adaptation flow and the AI ----------

    def adapt(self, text='1. Subraya los sustantivos. 2. Escribe tres. 3. Lee. 4. Clasifica. 5. Explica.'):
        material = OriginalMaterial.objects.create(teacher=self.teacher, subject=self.language, title='Sustantivos',
                                                   topic='Sustantivos del cuento', input_type='TEXT', input_text=text)
        fake = FakeGemini()
        with mock.patch.object(ai, 'generate_json', fake):
            services.process_material(material.pk, [(self.student.pk, self.student.conditions)],
                                      {self.student.pk: ['SEGMENTATION']})
        material.refresh_from_db()
        return material, fake

    def test_19_works_with_the_relevance_engine(self):
        past = self.worksheet(topic='Sustantivos del cuento',
                              content={'relevance': {'relevant_supports': [{'label': 'instrucciones breves o segmentadas'}]}})
        self.evaluate(past, 1, comprehension='DIFFICULTY', supports_applied=['instrucciones_breves'])
        material, _fake = self.adapt()
        self.assertEqual(material.status, MaterialStatus.PROCESSED, material.ai_error)
        content = material.adaptations.order_by('-created_at').first().content
        self.assertIn('relevance', content)  # the relevance engine still runs and is stored as before
        self.assertIn('nee', content['relevance'])
        history = content['learning_history']
        self.assertEqual((history['count'], history['comparable_count']), (1, 1))
        self.assertTrue(history['related'][0]['comparable'])
        self.assertEqual(history['related'][0]['applied'], ['Instrucciones breves o segmentadas'])
        summary = progress.supports_summary(progress.history(self.student))
        self.assertEqual(summary['suggested'][0]['label'], 'instrucciones breves o segmentadas')  # from the engine
        self.client.force_login(self.teacher)
        page = self.client.get(reverse('material_detail', args=[material.pk])).content.decode()
        self.assertIn('Antecedentes del historial de aprendizaje', page)

    def test_20_no_history_reaches_gemini(self):
        past = self.worksheet(topic='Tema histórico Zeta')
        self.evaluate(past, 1, observation=SECRET, comprehension='NO', goal_attainment='MUCH_LESS',
                      supports_applied=['pausas'], supports_note=NOTE)
        _material, fake = self.adapt()
        # Not even with the profile authorization and the setting on: it does not cover past evaluations
        StudentConsent.objects.create(student=self.student, document_version='IA-PERFIL-PRUEBA',
                                      authorized_on=date(2026, 11, 1),
                                      treatments=[Treatment.INTERNAL, Treatment.AI_ADAPTATION, Treatment.AI_PROFILE])
        with self.settings(AI_PROFILE_TRANSFER_ENABLED=True):
            _material, authorized = self.adapt()
        for sent in (fake.everything(), authorized.everything()):
            for private in ('Tema histórico Zeta', past.code, 'se cansó', 'voz alta', 'Mucho menos de lo esperado',
                            'No comprendió', 'Historial', 'historial', 'Pausas programadas', 'Antecedentes'):
                self.assertNotIn(private, sent)

    # ---------- 21-22: existing records and empty profiles ----------

    def test_21_existing_records_are_kept(self):
        adaptation = self.worksheet()
        evaluation = self.evaluate(adaptation, 1, goal_attainment='LESS', comprehension='DIFFICULTY', observation=SECRET)
        before = Evaluation.objects.filter(pk=evaluation.pk).values(
            'goal_attainment', 'comprehension', 'autonomy_level', 'status', 'created_at', 'updated_at').get()
        progress.progress(self.student)
        progress.adaptation_context(self.student, 'LEN')
        self.page()
        evaluation.refresh_from_db()
        self.assertEqual(Evaluation.objects.filter(pk=evaluation.pk).values(*before).get(), before)  # read only
        self.assertEqual((evaluation.supports_applied, evaluation.supports_useful, evaluation.supports_note),
                         ([], [], ''))  # an evaluation without the new fields: "not recorded", never inferred
        self.assertEqual(evaluation.observation, SECRET)
        draft = self.evaluate(self.worksheet(topic='Borrador'), 2, status=EvaluationStatus.DRAFT, comprehension='')
        data = progress.progress(self.student)
        self.assertEqual((data['total'], data['drafts']), (1, 1))  # drafts are kept but not analyzed
        self.assertTrue(Evaluation.objects.filter(pk=draft.pk).exists())
        adaptation.record_status = 'INACTIVE'
        adaptation.save()
        self.assertFalse(progress.history(self.student)[0].worksheet_active)  # a deleted worksheet keeps its history

    def test_22_works_with_an_empty_profile(self):
        self.assertEqual((self.student.learning_strengths, self.student.learning_barriers,
                          self.student.pedagogical_supports), ('', '', ''))
        self.assertEqual(progress.adaptation_context(self.student, 'LEN')['count'], 0)
        material, _fake = self.adapt()
        self.assertEqual(material.status, MaterialStatus.PROCESSED, material.ai_error)
        self.evaluate(material.adaptations.first(), 2)
        self.assertEqual(self.page().status_code, 200)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse('adapt')).status_code, 200)
