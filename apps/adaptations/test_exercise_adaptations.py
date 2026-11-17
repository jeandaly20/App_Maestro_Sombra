"""
Corrections per exercise: the worksheet is not the original rewritten, each exercise is corrected for the student
(fewer repeated items, data already chosen, smaller numbers...) and the teacher sees why and what to prepare.
Gemini simulated; made-up material only.
"""
from unittest import mock

from django.test import SimpleTestCase

from apps.core.choices import Condition

from . import ai, lesson_plan, nee_modules
from .tests import FakeGemini, worksheet_answer

DIVISIONS = ('Usamos la calculadora. 1. Decide si estas divisiones son exactas: 6540 : 7, 4792 : 4, 345 : 15, '
             '673 : 24. 2. Completa con 3 números consecutivos y multiplica. 3. Calcula 102564 x 4. ¿Qué observas?')
ANALYSIS = {'extracted_text': DIVISIONS, 'figures': [], 'detected_barriers': ['números grandes'], 'exercise_count': 3}


def adapt(conditions, answer=None):
    fake = FakeGemini()
    with mock.patch.object(ai, 'generate_json', side_effect=lambda system, parts, schema: (
            answer or worksheet_answer(schema), 'fake') if 'concepts' in schema['properties'] else fake(system, parts, schema)) as call:
        content, _model = ai.adapt_worksheet(analysis=ANALYSIS, subject='Matemática', topic='Divisiones', grade='EGB_5',
                                             conditions=conditions, adjustments=[], subject_code='MAT')
    system, parts, schema = call.call_args_list[0].args
    return content, system + parts[0]['text'], schema


class PromptTests(SimpleTestCase):
    def test_the_ai_is_asked_to_correct_each_exercise_not_to_keep_every_number(self):
        _content, prompt, schema = adapt([Condition.DYSCALCULIA])
        self.assertNotIn('Nunca agregues ni cambies números', prompt)
        self.assertIn('Adaptar NO es copiar la guía', prompt)
        self.assertIn('exercise_adaptations', schema['required'])
        self.assertIn('dalos ya elegidos', prompt)          # dyscalculia in Mathematics
        self.assertIn('si no sale punto, es exacta', prompt)  # the calculator rule

    def test_each_condition_brings_its_own_math_corrections(self):
        _content, prompt, _schema = adapt([Condition.ID_MILD])
        self.assertIn('números de 1 o 2 cifras', prompt)
        self.assertIn('deja escrita la descomposición', prompt)
        _content, prompt, _schema = adapt([Condition.DYSLEXIA, Condition.ADHD])
        self.assertIn('grupos de 3 con un espacio', prompt)
        self.assertIn('Un solo ejercicio a la vista', prompt)

    def test_math_corrections_are_not_sent_for_other_subjects(self):
        self.assertNotIn('tabla pitagórica', ai.rules_for([Condition.DYSCALCULIA], [], 'LEN', 'EGB_5'))

    def test_the_action_label_is_stored_for_the_teacher(self):
        content, _prompt, _schema = adapt([Condition.DYSCALCULIA])
        self.assertEqual(content['exercise_adaptations'][0]['action_label'], 'Se reduce')


class CheckTests(SimpleTestCase):
    def test_counting_more_than_20_objects_is_flagged(self):
        self.assertTrue(nee_modules.counts_too_many('6540 bloques repartidos en 7 grupos iguales.'))
        self.assertTrue(nee_modules.counts_too_many('Cuadrícula de 34 filas por 35 columnas.'))
        self.assertFalse(nee_modules.counts_too_many('12 tapas repartidas en 3 vasos.'))
        self.assertFalse(nee_modules.counts_too_many('Tabla de valor posicional: 6 unidades de mil, 5 centenas.'))

    def test_a_worksheet_without_corrections_per_exercise_is_sent_back(self):
        content = worksheet_answer({'properties': {}})
        content['exercise_adaptations'] = []
        self.assertTrue(any('exercise_adaptations' in p for p in nee_modules.problems(content, [Condition.ADHD])))
        content['exercise_adaptations'] = [{'exercise': 'Ejercicio 1', 'barrier': 'b', 'action': 'MANTIENE',
                                            'adaptation': 'Igual.', 'teacher_support': 'Apoyo.'}]
        self.assertTrue(any('no dicen qué se cambió' in p for p in nee_modules.problems(content, [Condition.ADHD])))

    def test_the_exercises_of_the_original_are_counted_and_all_must_be_explained(self):
        text = ('1. Decide si son exactas. 2. Multiplica y divide el resultado entre 6. Haz la suma. 3. Calcula 102564 x 4. '
                'Practicamos. 1. Calcula 34 x 35. 2. Calcula 28 x 26.')
        self.assertEqual(nee_modules.exercise_count(text), 5)
        content = worksheet_answer({'properties': {}})
        problems = nee_modules.problems(content, [Condition.ADHD], expected_exercises=5)
        self.assertTrue(any('tiene 5 ejercicios numerados' in p for p in problems))

    def test_big_numbers_are_sent_back_for_intellectual_disability_in_math(self):
        content = worksheet_answer({'properties': {}})
        content['steps'][0].update(instruction='Divide 6540 entre 7.', worked_example='8 : 2 = 4')
        content['glossary'] = [{'term': 'dividir', 'meaning': 'repartir'}]
        self.assertTrue(any('4 cifras' in p for p in nee_modules.problems(content, [Condition.ID_MILD], subject_code='MAT')))
        self.assertFalse(any('4 cifras' in p for p in nee_modules.problems(content, [Condition.DYSCALCULIA],
                                                                             subject_code='MAT')))

    def test_an_absurd_concrete_support_is_sent_back(self):
        content = worksheet_answer({'properties': {}})
        content['steps'][0].update(instruction='Divide 6540 entre 7.', concrete_support='6540 bloques en 7 grupos.')
        self.assertTrue(any('más de 20 objetos' in p for p in nee_modules.problems(content, [Condition.DYSCALCULIA])))


class ClassTests(SimpleTestCase):
    def test_the_class_adaptation_receives_the_worksheet_corrections(self):
        fake = FakeGemini()
        worksheet = worksheet_answer({'properties': {}})
        with mock.patch.object(ai, 'generate_json', fake):
            ai.adapt_lesson(lesson={'title': 'Divisiones', 'moments': [{'name': 'Inicio', 'activities': 'Leer.'}]},
                            book_summary='Divisiones', subject='Matemática', subject_code='MAT', grade='EGB_5',
                            conditions=[Condition.DYSCALCULIA], adjustments=[], worksheet=worksheet)
        self.assertIn('Correcciones ya hechas en la ficha del alumno', fake.everything())
        self.assertIn('Subraya 3 sustantivos en lugar de 6.', fake.everything())

    def test_the_downloads_add_the_corrections_per_exercise(self):
        item = worksheet_answer({'properties': {}})['exercise_adaptations'][0]
        sections = lesson_plan.class_sections(analysis={}, content={}, subject='Matemática', grade='5.º EGB',
                                              pseudonym='E-01', condition='Discalculia', adaptation_grade='Grado 2',
                                              plan_row='', exercises=[item])
        kind, title, rows = sections[-1]
        self.assertEqual(title, '6. Adaptaciones específicas por ejercicio (ficha del alumno)')
        self.assertIn('Ventana de cartulina', rows[0][1])
        self.assertEqual(len(lesson_plan.class_sections(analysis={}, content={}, subject='M', grade='g', pseudonym='p',
                                                        condition='c', adaptation_grade='a', plan_row='')), 5)
