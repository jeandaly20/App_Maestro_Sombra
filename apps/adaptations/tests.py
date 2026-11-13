"""
Task 3: the pedagogical relevance engine and its connection to the worksheet and class adaptations.
Gemini is simulated (no external calls); made-up students and texts only.
"""
import copy
import json
from datetime import date
from itertools import combinations
from unittest import mock

from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Teacher
from apps.core.choices import INTELLECTUAL_DISABILITY, LEARNING_DISORDERS, Condition
from apps.materials.models import MaterialStatus, OriginalMaterial, Subject
from apps.students.models import Student, ai_profile_allowed

from . import ai, pedagogical_relevance as engine, services
from .models import LessonPlan

STRENGTHS = 'Comprende muy bien las explicaciones orales y le gustan los cuentos de animales.'
BARRIERS = 'Le cuesta procesar cantidades y mantener la atención en actividades largas.'
SUPPORTS = 'Pausas programadas, instrucciones breves y material concreto.'
PROFILE = {'learning_strengths': STRENGTHS, 'learning_barriers': BARRIERS, 'pedagogical_supports': SUPPORTS}
NOUNS = ('Lee el texto y subraya los sustantivos. 1. Subraya los sustantivos propios. 2. Escribe tres sustantivos '
         'comunes. 3. Clasifica: perro, Quito, mesa. 4. Escribe una oración. 5. Explica qué es un sustantivo.')
FRACTIONS = ('1. Reparte 12 caramelos entre 3 niños. 2. Calcula 3/4 de 8. 3. Compara 1/2 y 1/4. 4. Resuelve 24 x 3. '
             '5. ¿Cuántos grupos de 4 hay en 20?')
ALL = [c.value for c in Condition]
SUPPORTED = [list(c) for size in range(1, 7) for c in combinations(ALL, size)
             if not (set(c) & set(LEARNING_DISORDERS) and set(c) & set(INTELLECTUAL_DISABILITY))
             and not set(INTELLECTUAL_DISABILITY) <= set(c)]


def run(conditions, profile=None, subject='LEN', text=NOUNS, **extra):
    names = {'LEN': 'Lengua y Literatura', 'MAT': 'Matemática', 'CIE': 'Ciencias Naturales'}
    options = {'exercise_count': 5}
    options.update(extra)
    return engine.analyze(conditions=conditions, profile=profile or {}, subject_code=subject,
                          subject_name=names[subject], topic='Tema de prueba', activity_text=text, **options)


def by_condition(result):
    return {item['condition']: item['applies'] for item in result['nee']}


class RelevanceEngineTests(TestCase):
    def test_01_complete_profile(self):
        result = run(['DYSCALCULIA', 'ADHD'], PROFILE)
        self.assertEqual([b['category'] for b in result['relevant_barriers']], ['atencion'])
        self.assertIn('calculo', [b['category'] for b in result['other_barriers']])
        self.assertIn('oral', [s['category'] for s in result['usable_strengths']])
        self.assertIn('pausas', [s['category'] for s in result['relevant_supports']])
        self.assertTrue(all(item['reason'] for key in ('relevant_barriers', 'usable_strengths', 'relevant_supports')
                            for item in result[key]))
        self.assertIn(engine.PRIVACY_NOTE, result['warnings'])

    def test_02_empty_profile(self):
        result = run(['DYSLEXIA'])
        self.assertEqual((result['relevant_barriers'], result['usable_strengths'], result['relevant_supports']), ([], [], []))
        self.assertTrue(any('perfil pedagógico está vacío' in w for w in result['warnings']))

    def test_03_partial_profile(self):
        result = run(['ADHD'], {'learning_barriers': BARRIERS})
        self.assertTrue(any('falta registrar fortalezas, apoyos' in w for w in result['warnings']))

    def test_04_same_nee_different_barriers(self):
        reading = run(['DYSLEXIA'], {'learning_barriers': 'Se confunde al leer palabras largas.'})
        attention = run(['DYSLEXIA'], {'learning_barriers': 'Se distrae con facilidad.'})
        self.assertEqual([b['category'] for b in reading['relevant_barriers']], ['lectura'])
        self.assertEqual([b['category'] for b in attention['relevant_barriers']], ['atencion'])

    def test_05_dyscalculia_adhd_in_language(self):
        result = run(['DYSCALCULIA', 'ADHD'], PROFILE, subject='LEN', text=NOUNS)
        self.assertEqual(by_condition(result), {'DYSCALCULIA': False, 'ADHD': True})
        self.assertIn('DEA: Discalculia: no aplica', result['ai_prompt'])
        self.assertIn('No agregues contenidos de otra materia', result['ai_prompt'])
        self.assertNotIn('material_concreto', [s['category'] for s in result['relevant_supports']])

    def test_06_dyscalculia_adhd_in_mathematics(self):
        result = run(['DYSCALCULIA', 'ADHD'], PROFILE, subject='MAT', text=FRACTIONS)
        self.assertEqual(by_condition(result), {'DYSCALCULIA': True, 'ADHD': True})
        self.assertEqual({b['category'] for b in result['relevant_barriers']}, {'calculo', 'atencion'})
        self.assertIn('material_concreto', [s['category'] for s in result['relevant_supports']])

    def test_07_dyslexia_adhd_with_specific_barriers(self):
        profile = {'learning_barriers': 'Lee con mucho esfuerzo los textos y se distrae al copiar.',
                   'pedagogical_supports': 'Lectura acompañada y pausas activas.'}
        result = run(['DYSLEXIA', 'ADHD'], profile, text='Lee el cuento y escribe el final. ' * 3)
        self.assertEqual(by_condition(result), {'DYSLEXIA': True, 'ADHD': True})
        self.assertIn('lectura', [b['category'] for b in result['relevant_barriers']])
        self.assertIn('lectura_acompanada', [s['category'] for s in result['relevant_supports']])

    def test_08_works_with_the_23_combinations(self):
        self.assertEqual(len(SUPPORTED), 23)
        for conditions in SUPPORTED:
            with self.subTest(conditions=conditions):
                result = run(conditions, PROFILE)
                self.assertEqual(len(result['nee']), len(conditions))
                for private in (STRENGTHS, BARRIERS, SUPPORTS):
                    self.assertNotIn(private, result['ai_prompt'])

    def test_09_curricular_objectives_are_kept(self):
        result = run(['ADHD'], is_lesson=True, objective='Multiplicar números de dos cifras.',
                     skill='M.3.1.7. Resolver multiplicaciones.')
        self.assertIn('Conserva el objetivo curricular: M.3.1.7. Resolver multiplicaciones. Multiplicar números',
                      result['ai_prompt'])
        planned = {'moments': [{'name': 'Inicio', 'duration': '10 min'}, {'name': 'Desarrollo', 'duration': '20 min'}]}
        adapted = {'adapted_objective': '', 'evaluation': 'Observación.',
                   'moments': [{'name': 'Inicio', 'duration': '15 min'}]}
        problems = engine.review_checks(adapted, kind='lesson', conditions=['ADHD'], original=planned)['problems']
        self.assertTrue(any('no tiene objetivo adaptado' in p for p in problems))
        self.assertTrue(any('1 momentos y la clase planificada 2' in p for p in problems))
        self.assertTrue(any('cambió la duración de: Inicio' in p for p in problems))

    def test_storable_result_has_no_free_text(self):
        saved = json.dumps(engine.storable(run(['DYSCALCULIA', 'ADHD'], PROFILE)), ensure_ascii=False)
        for private in (STRENGTHS, BARRIERS, SUPPORTS):
            self.assertNotIn(private, saved)
        self.assertNotIn('ai_prompt', saved)


def worksheet_answer(schema):
    content = {'title': 'Ficha', 'summary': 's', 'intro': 'Esta ficha tiene 5 pasos.', 'learning_goal': 'Vas a aprender.',
               'concepts': [], 'check_questions': [], 'udl_principle': 'p', 'udl_justification': 'j',
               'steps': [{'title': 'Subraya', 'instruction': 'Subraya los sustantivos.', 'hint': 'Busca nombres.',
                          'keyword': 'subraya', 'answer_type': 'ESPACIO', 'minutes': 4}],
               'active_break': 'Estírate.', 'break_after_step': 1}
    if 'pedagogical_justification' in schema['properties']:
        content['pedagogical_justification'] = [
            {'barrier': 'Atención sostenida (TDAH)', 'adjustment': 'Bloques de 4 minutos', 'reason': 'Muchos ejercicios.',
             'objective': 'Reconocer sustantivos.', 'indicator': 'Termina cada bloque sin ayuda.'}]
    return content


def lesson_answer(schema):
    content = {'suggested_grade': 'NON_SIGNIFICANT', 'grade_reason': 'r', 'adapted_objective': 'Reconocer sustantivos.',
               'plan_row': ['Segmentar.'], 'evaluation': 'Con el indicador de la clase.',
               'moments': [{'name': 'Inicio', 'duration': '10 min', 'teacher_actions': ['t'], 'student_task': 's',
                            'supports': ['x']}]}
    if 'pedagogical_justification' in schema['properties']:
        content['pedagogical_justification'] = worksheet_answer(schema)['pedagogical_justification']
    return content


class FakeGemini:
    """Records every request (system + parts + schema) and answers like Gemini would."""

    def __init__(self):
        self.requests = []

    def __call__(self, system, parts, schema):
        self.requests.append({'text': system + ' '.join(str(p.get('text', '')) for p in parts), 'schema': schema})
        props = schema['properties']
        if 'extracted_text' in props:
            return {'readable': True, 'extracted_text': 'Lee: Zoila Quintanilla vive en Quito. Subraya los sustantivos.',
                    'figures': [], 'detected_barriers': [], 'cognitive_load': 'MEDIA', 'summary': 'Sustantivos',
                    'title': 'Sustantivos', 'detected_subject': 'LEN', 'detected_level': 'MEDIA', 'handwritten': [],
                    'exercise_count': 5, 'matches_lesson': True}, 'fake'
        if 'skill' in props:
            return {'title': 'Sustantivos', 'skill': 'LL.3.4.6. Usar sustantivos.', 'objective': 'Reconocer sustantivos.',
                    'summary': 'Clase', 'moments': [{'name': 'Inicio', 'duration': '10 min', 'activities': 'Leer.'}]}, 'fake'
        if 'concepts' in props:
            return worksheet_answer(schema), 'fake'
        return lesson_answer(schema), 'fake'

    def everything(self):
        return ' '.join(request['text'] for request in self.requests)


class AdaptationIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.pertinencia@maestrosombra.test', None,
                                                  full_name='Docente de Prueba', accepted_terms=True,
                                                  terms_accepted_at=timezone.now())
        cls.student = Student(teacher=cls.teacher, name='Zoila Quintanilla', birth_date=date(2016, 3, 1), grade='EGB_5',
                              conditions=['DYSCALCULIA', 'ADHD'], diagnosis_source='IN_EVALUATION',
                              notes='Nota privada del docente.', **PROFILE)
        cls.student.save()
        cls.language = Subject.objects.get(code='LEN')

    def material(self, text=NOUNS):
        return OriginalMaterial.objects.create(teacher=self.teacher, subject=self.language, title='Sustantivos',
                                               topic='El sustantivo', input_type='TEXT', input_text=text)

    def adapt_material(self, text=NOUNS):
        fake = FakeGemini()
        material = self.material(text)
        with mock.patch.object(ai, 'generate_json', fake):
            services.process_material(material.pk, [(self.student.pk, self.student.conditions)], {self.student.pk: []})
        material.refresh_from_db()
        return material, fake

    def test_10_worksheet_adaptation_uses_the_engine(self):
        material, fake = self.adapt_material()
        self.assertEqual(material.status, MaterialStatus.PROCESSED)
        content = material.adaptations.get().content
        self.assertIn('relevance', content)
        self.assertTrue(content['review_checks']['teacher_review_required'])
        self.assertEqual(content['pedagogical_justification'][0]['indicator'], 'Termina cada bloque sin ayuda.')
        worksheet_request = next(r for r in fake.requests if 'concepts' in r['schema']['properties'])
        self.assertIn('Pertinencia de las NEE para esta actividad', worksheet_request['text'])
        self.assertIn('pedagogical_justification', worksheet_request['schema']['required'])

    def test_11_class_adaptation_uses_the_engine(self):
        book = self.material('Página del libro: subraya los sustantivos del cuento.')
        lesson = LessonPlan.objects.create(teacher=self.teacher, subject=self.language, material=book,
                                           plan_file=ContentFile(b'plan', name='plan.xlsx'), session_label='Semana 1',
                                           title='Sustantivos', session_text='Semana 1. Destreza LL.3.4.6.')
        fake = FakeGemini()
        with mock.patch.object(ai, 'generate_json', fake):
            services.process_lesson(lesson.pk, [(self.student.pk, self.student.conditions)], {self.student.pk: []},
                                    check_topic=False)
        lesson.refresh_from_db()
        self.assertEqual(lesson.status, MaterialStatus.PROCESSED, lesson.ai_error)
        content = lesson.adaptations.get().content
        self.assertIn('Conserva el objetivo curricular', next(
            r['text'] for r in fake.requests if 'plan_row' in r['schema']['properties']))
        self.assertTrue(content['relevance']['nee'])
        self.assertEqual(content['review_checks']['problems'], [])

    def test_12_json_schemas_stay_compatible(self):
        before = (copy.deepcopy(ai.ADAPTATION_SCHEMA), copy.deepcopy(ai.LESSON_ADAPTATION_SCHEMA))
        extended = ai.with_justification(ai.LESSON_ADAPTATION_SCHEMA)
        self.assertIn('pedagogical_justification', extended['required'])
        self.assertEqual((ai.ADAPTATION_SCHEMA, ai.LESSON_ADAPTATION_SCHEMA), before)  # the base schemas never change
        material, _ = self.adapt_material()
        adaptation = material.adaptations.get()
        self.client.force_login(self.teacher)
        page = self.client.get(f'/adaptar/material/{material.pk}/?a={adaptation.pk}')
        self.assertContains(page, 'Pertinencia pedagógica y comprobaciones')
        adaptation.content = {key: value for key, value in adaptation.content.items()
                              if key not in ('relevance', 'review_checks', 'pedagogical_justification')}
        adaptation.save()  # an adaptation made before this task still opens
        self.assertEqual(self.client.get(f'/adaptar/material/{material.pk}/?a={adaptation.pk}').status_code, 200)

    def test_13_invalid_answers_are_rejected_or_flagged(self):
        with mock.patch.object(ai, '_call', return_value={'candidates': [{'content': {'parts': [{'text': 'no es JSON'}]}}]}), \
                mock.patch.object(ai.time, 'sleep'), self.settings(GEMINI_API_KEY='clave-de-prueba'):
            with self.assertRaises(ai.AIError):
                ai.generate_json('s', [{'text': 't'}], {'type': 'OBJECT', 'properties': {}})
        with mock.patch.object(ai, 'generate_json', return_value=({'moments': [], 'plan_row': []}, 'fake')):
            with self.assertRaises(ai.AIError):
                ai.adapt_lesson(lesson={'moments': []}, book_summary='', subject='Lengua', subject_code='LEN',
                                grade='EGB_5', conditions=['ADHD'], adjustments=[])
        flagged = engine.review_checks(
            {'learning_goal': 'Vas a aprender.', 'steps': [],
             'pedagogical_justification': [{'barrier': 'Dislexia severa', 'adjustment': 'x', 'reason': '', 'indicator': ''}]},
            kind='worksheet', conditions=['ADHD'])['problems']
        self.assertTrue(any('motivo o indicador' in p for p in flagged))
        self.assertTrue(any('no tiene registrados: dislexia' in p for p in flagged))

    def test_14_personal_information_is_protected(self):
        material, fake = self.adapt_material('Zoila Quintanilla: subraya los sustantivos. ' + NOUNS)
        everything = fake.everything()
        for private in ('Zoila', 'Quintanilla', 'Nota privada del docente.', '2016'):
            self.assertNotIn(private, everything)
        self.assertNotIn('Quintanilla', material.ai_analysis['extracted_text'])
        named = engine.review_checks({'learning_goal': 'Vas a aprender, Zoila.', 'steps': []}, kind='worksheet',
                                     conditions=['ADHD'], student_name='Zoila Quintanilla')['problems']
        self.assertTrue(any('nombre del estudiante' in p for p in named))

    def test_15_the_profile_is_not_sent_without_authorization(self):
        self.assertFalse(ai_profile_allowed(self.student))  # no individual authorization covering it
        material, fake = self.adapt_material()
        everything = fake.everything()
        for private in (STRENGTHS, BARRIERS, SUPPORTS, 'pausas programadas'):
            self.assertNotIn(private.lower(), everything.lower())
        self.assertFalse(material.adaptations.get().content['relevance']['profile_shared_with_ai'])

    def test_16_previous_behaviour_without_relevance(self):
        fake = FakeGemini()
        analysis = {'extracted_text': 'Subraya los sustantivos.', 'figures': []}
        with mock.patch.object(ai, 'generate_json', fake):
            content, _ = ai.adapt_worksheet(analysis=analysis, subject='Lengua', topic='Sustantivos', grade='EGB_5',
                                            conditions=['ADHD'], adjustments=[], subject_code='LEN')
        self.assertNotIn('pedagogical_justification', fake.requests[0]['schema']['properties'])
        self.assertNotIn('Pertinencia de las NEE', fake.requests[0]['text'])
        self.assertNotIn('pedagogical_justification', content)
