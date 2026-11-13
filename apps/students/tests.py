"""
Accepted SEN (NEE) combinations of the student record: the 23 this version supports, the ones it does not
cover yet, and the wording of the messages (the scope of the system, not a clinical statement).
Made-up students only: no documents and no personal data.
"""
from datetime import date
from itertools import combinations
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Teacher
from apps.core.choices import INTELLECTUAL_DISABILITY, LEARNING_DISORDERS, Condition

from .models import UNSUPPORTED_COMBINATION, Student

ALL = [condition.value for condition in Condition]


def supported(combination):
    """The rule of this version: no DEA with intellectual disability, and a single intellectual disability level."""
    chosen = set(combination)
    return not (chosen & set(LEARNING_DISORDERS) and chosen & set(INTELLECTUAL_DISABILITY)) and \
        not set(INTELLECTUAL_DISABILITY) <= chosen


EVERY_COMBINATION = [list(c) for size in range(1, len(ALL) + 1) for c in combinations(ALL, size)]
SUPPORTED = [c for c in EVERY_COMBINATION if supported(c)]
NOT_SUPPORTED = [c for c in EVERY_COMBINATION if not supported(c)]


def student_data(conditions, **extra):
    data = {'name': 'Estudiante de Prueba', 'birth_date': '2016-03-01', 'grade': 'EGB_5', 'conditions': conditions,
            'diagnosis_source': 'IN_EVALUATION', 'diagnosis_date': '', 'notes': ''}
    data.update(extra)
    return data


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class CombinationRulesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.pruebas@maestrosombra.test', None,
                                                  full_name='Docente de Prueba', accepted_terms=True,
                                                  terms_accepted_at=timezone.now())

    def build(self, conditions):
        return Student(teacher=self.teacher, name='Estudiante de Prueba', birth_date=date(2016, 3, 1),
                       grade='EGB_5', conditions=conditions, diagnosis_source='IN_EVALUATION')

    def test_there_are_23_supported_combinations(self):
        self.assertEqual(len(SUPPORTED), 23)
        self.assertEqual(len(NOT_SUPPORTED), 63 - 23)

    def test_every_supported_combination_is_valid(self):
        for conditions in SUPPORTED:
            with self.subTest(conditions=conditions):
                self.build(conditions).full_clean()

    def test_dea_with_intellectual_disability_is_rejected_with_a_scope_message(self):
        for learning in LEARNING_DISORDERS:
            for level in INTELLECTUAL_DISABILITY:
                with self.subTest(learning=learning, level=level):
                    with self.assertRaises(ValidationError) as raised:
                        self.build([learning, level]).full_clean()
                    self.assertEqual(raised.exception.message_dict['conditions'], [UNSUPPORTED_COMBINATION])

    def test_every_not_supported_combination_is_rejected(self):
        for conditions in NOT_SUPPORTED:
            with self.subTest(conditions=conditions):
                with self.assertRaises(ValidationError) as raised:
                    self.build(conditions).full_clean()
                self.assertIn('conditions', raised.exception.message_dict)

    def test_both_intellectual_disability_levels_are_rejected(self):
        with self.assertRaises(ValidationError) as raised:
            self.build(INTELLECTUAL_DISABILITY).full_clean()
        self.assertIn('un solo nivel', raised.exception.message_dict['conditions'][0])

    def test_the_message_states_the_scope_not_a_clinical_rule(self):
        self.assertIn('Esta versión de App Maestro Sombra todavía no contempla', UNSUPPORTED_COMBINATION)
        self.assertIn('consulta con el profesional responsable', UNSUPPORTED_COMBINATION)
        self.assertNotIn('DSM', UNSUPPORTED_COMBINATION)

    def test_the_database_still_blocks_a_manipulated_record(self):
        """Even if the form is bypassed (a request edited in the browser), PostgreSQL keeps the rule."""
        student = self.build([Condition.DYSLEXIA])
        student.save()
        for conditions in ([Condition.DYSLEXIA, Condition.ID_MILD], [Condition.DYSCALCULIA, Condition.ID_MODERATE],
                           list(INTELLECTUAL_DISABILITY)):
            with self.subTest(conditions=conditions):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Student.objects.filter(pk=student.pk).update(conditions=conditions)


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class RegisterAndEditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.registro@maestrosombra.test', None,
                                                  full_name='Docente de Prueba', accepted_terms=True,
                                                  terms_accepted_at=timezone.now())

    def setUp(self):
        self.client.force_login(self.teacher)

    def test_register_a_supported_combination(self):
        response = self.client.post(reverse('student_create'), student_data(['DYSLEXIA', 'ADHD', 'ASD']))
        self.assertRedirects(response, reverse('student_list'))
        self.assertEqual(Student.objects.get(teacher=self.teacher).conditions, ['DYSLEXIA', 'ADHD', 'ASD'])

    def test_register_a_not_supported_combination_shows_the_scope_message(self):
        response = self.client.post(reverse('student_create'), student_data(['DYSCALCULIA', 'ID_MILD']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'todavía no contempla adaptaciones')
        self.assertFalse(Student.objects.filter(teacher=self.teacher).exists())

    def test_editing_keeps_the_stored_conditions_when_the_change_is_rejected(self):
        student = Student(teacher=self.teacher, name='Estudiante de Prueba', birth_date=date(2016, 3, 1),
                          grade='EGB_5', conditions=['ID_MILD', 'ASD'], diagnosis_source='IN_EVALUATION')
        student.save()
        url = reverse('student_edit', args=[student.pk])
        response = self.client.post(url, student_data(['ID_MILD', 'ASD', 'DYSLEXIA']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'todavía no contempla adaptaciones')
        student.refresh_from_db()
        self.assertEqual(set(student.conditions), {'ID_MILD', 'ASD'})  # nothing removed or added

    def test_editing_to_another_supported_combination_works(self):
        student = Student(teacher=self.teacher, name='Estudiante de Prueba', birth_date=date(2016, 3, 1),
                          grade='EGB_5', conditions=['ADHD'], diagnosis_source='IN_EVALUATION')
        student.save()
        response = self.client.post(reverse('student_edit', args=[student.pk]),
                                    student_data(['ID_MODERATE', 'ADHD', 'ASD']))
        self.assertRedirects(response, reverse('student_list'))
        student.refresh_from_db()
        self.assertEqual(set(student.conditions), {'ID_MODERATE', 'ADHD', 'ASD'})

    def test_the_register_page_explains_the_scope(self):
        page = self.client.get(reverse('student_create')).content.decode()
        self.assertIn('Esta versión admite 23 combinaciones de NEE', page)
        self.assertIn('aún no están contempladas por el sistema', page)
        self.assertNotIn('no se combinan con discapacidad intelectual', page)


class FrontEndTextsTests(TestCase):
    def test_the_form_script_uses_the_scope_wording(self):
        script = (Path(settings.BASE_DIR) / 'static' / 'js' / 'students.js').read_text(encoding='utf-8')
        self.assertIn('Esta versión aún no contempla combinar dislexia o discalculia con discapacidad intelectual', script)
        self.assertNotIn('DSM-5', script)
        # The script only disables boxes that are not ticked: it never unticks a chosen condition
        self.assertIn('!box.checked &&', script)
        self.assertNotIn('box.checked = false', script)
