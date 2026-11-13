"""
Student record tests. Made-up students only: no documents and no personal data.

- Task 1: accepted SEN (NEE) combinations (the 23 this version supports, the ones it does not cover yet) and
  the wording of the messages (the scope of the system, not a clinical statement).
- Task 2: the individual pedagogical profile (strengths, barriers, individual supports).
"""
from datetime import date
from itertools import combinations
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Teacher
from apps.core.choices import INTELLECTUAL_DISABILITY, LEARNING_DISORDERS, Condition
from apps.core.fields import decrypt_bytes

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


# ---------------- Task 2: individual pedagogical profile ----------------

STRENGTHS = 'Comprende explicaciones orales y reconoce imágenes con facilidad.'
BARRIERS = 'Le cuesta leer instrucciones extensas y mantener la atención en actividades largas.'
SUPPORTS = 'Instrucciones breves, lectura acompañada, apoyos visuales y pausas programadas.'
PROFILE = {'learning_strengths': STRENGTHS, 'learning_barriers': BARRIERS, 'pedagogical_supports': SUPPORTS}


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class PedagogicalProfileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.perfil@maestrosombra.test', None,
                                                  full_name='Docente de Prueba', accepted_terms=True,
                                                  terms_accepted_at=timezone.now())
        cls.other = Teacher.objects.create_user('otra.docente@maestrosombra.test', None,
                                                full_name='Otra Docente', accepted_terms=True,
                                                terms_accepted_at=timezone.now())

    def setUp(self):
        self.client.force_login(self.teacher)

    def make_student(self, conditions=('ADHD',), **fields):
        student = Student(teacher=self.teacher, name='Estudiante de Prueba', birth_date=date(2016, 3, 1),
                          grade='EGB_5', conditions=list(conditions), diagnosis_source='IN_EVALUATION', **fields)
        student.save()
        return student

    def edit(self, student, **changes):
        data = student_data(student.conditions, notes=student.notes,
                            **{field: getattr(student, field) for field in PROFILE})
        data.update(changes)
        return self.client.post(reverse('student_edit', args=[student.pk]), data)

    def test_01_register_without_profile(self):
        response = self.client.post(reverse('student_create'), student_data(['DYSLEXIA']))
        self.assertRedirects(response, reverse('student_list'))
        student = Student.objects.get(teacher=self.teacher)
        self.assertEqual((student.learning_strengths, student.learning_barriers, student.pedagogical_supports),
                         ('', '', ''))

    def test_02_register_with_the_three_fields(self):
        response = self.client.post(reverse('student_create'), student_data(['DYSLEXIA', 'ADHD'], **PROFILE))
        self.assertRedirects(response, reverse('student_list'))
        student = Student.objects.get(teacher=self.teacher)
        self.assertEqual(student.learning_strengths, STRENGTHS)
        self.assertEqual(student.learning_barriers, BARRIERS)
        self.assertEqual(student.pedagogical_supports, SUPPORTS)

    def test_03_04_05_each_field_is_edited_on_its_own(self):
        student = self.make_student(**PROFILE)
        for field, new_text in (('learning_strengths', 'Le gusta dibujar y explicar con sus palabras.'),
                                ('learning_barriers', 'Se distrae con el ruido del patio.'),
                                ('pedagogical_supports', 'Ubicarlo cerca del docente y dar una consigna a la vez.')):
            with self.subTest(field=field):
                before = {name: getattr(student, name) for name in PROFILE}
                self.assertRedirects(self.edit(student, **{field: new_text}), reverse('student_list'))
                student.refresh_from_db()
                self.assertEqual(getattr(student, field), new_text)
                for other in PROFILE:
                    if other != field:
                        self.assertEqual(getattr(student, other), before[other])

    def test_06_the_profile_is_shown_again_when_editing(self):
        student = self.make_student(**PROFILE)
        page = self.client.get(reverse('student_edit', args=[student.pk])).content.decode()
        self.assertIn('4. Perfil pedagógico individual', page)
        for text in (STRENGTHS, BARRIERS, SUPPORTS):
            self.assertIn(text, page)

    def test_07_the_profile_is_encrypted_in_the_database(self):
        student = self.make_student(**PROFILE)
        with connection.cursor() as cursor:
            cursor.execute('SELECT learning_strengths, learning_barriers, pedagogical_supports FROM mst_student '
                           'WHERE id = %s', [student.pk])
            stored = cursor.fetchone()
        for raw, plain in zip(stored, (STRENGTHS, BARRIERS, SUPPORTS)):
            self.assertNotIn(plain, raw)
            self.assertTrue(raw.startswith('gAAAA'))  # a Fernet token, like the other encrypted fields
            self.assertEqual(decrypt_bytes(raw.encode()).decode(), plain)

    def test_08_only_the_student_teacher_can_see_or_change_the_profile(self):
        student = self.make_student(**PROFILE)
        url = reverse('student_edit', args=[student.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, student_data(['ADHD'], learning_strengths='cambiado')).status_code, 404)
        self.assertNotIn(STRENGTHS, self.client.get(reverse('student_list')).content.decode())
        student.refresh_from_db()
        self.assertEqual(student.learning_strengths, STRENGTHS)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)  # to the sign-in page

    def test_08b_the_list_shows_how_complete_the_profile_is_not_its_content(self):
        self.make_student(learning_strengths=STRENGTHS)
        page = self.client.get(reverse('student_list')).content.decode()
        self.assertIn('En elaboración (1 de 3 apartados)', page)
        self.assertNotIn(STRENGTHS, page)

    def test_09_the_profile_works_with_the_23_combinations(self):
        for conditions in SUPPORTED:
            with self.subTest(conditions=conditions):
                Student(teacher=self.teacher, name='Estudiante de Prueba', birth_date=date(2016, 3, 1),
                        grade='EGB_5', conditions=conditions, diagnosis_source='IN_EVALUATION', **PROFILE).full_clean()

    def test_10_the_notes_are_kept(self):
        student = self.make_student(notes='Observación previa del docente.')
        self.assertRedirects(self.edit(student, learning_barriers=BARRIERS), reverse('student_list'))
        student.refresh_from_db()
        self.assertEqual(student.notes, 'Observación previa del docente.')
        self.assertEqual(student.learning_barriers, BARRIERS)

    def test_11_changing_the_nee_keeps_the_individual_supports(self):
        student = self.make_student(conditions=['ADHD'], **PROFILE)
        self.assertRedirects(self.edit(student, conditions=['DYSCALCULIA', 'ASD']), reverse('student_list'))
        student.refresh_from_db()
        self.assertEqual(set(student.conditions), {'DYSCALCULIA', 'ASD'})
        self.assertEqual(student.pedagogical_supports, SUPPORTS)
        self.assertEqual(student.learning_strengths, STRENGTHS)

    def test_12_the_profile_is_not_sent_to_gemini(self):
        """A whole worksheet adaptation: no prompt sent to the AI, and no log line, carries the profile."""
        from apps.adaptations import ai, services
        from apps.materials.models import OriginalMaterial, Subject

        student = self.make_student(conditions=['DYSLEXIA'], notes='Nota privada.', **PROFILE)
        material = OriginalMaterial.objects.create(teacher=self.teacher, subject=Subject.objects.get(code='LEN'),
                                                   title='Sustantivos', topic='El sustantivo', input_type='TEXT',
                                                   input_text='Subraya los sustantivos: Ana vive en Quito.')
        sent = []

        def fake_generate_json(system, parts, schema):
            sent.append(system + ' '.join(str(part.get('text', '')) for part in parts))
            if 'extracted_text' in schema['properties']:
                return {'readable': True, 'extracted_text': 'Subraya los sustantivos.', 'figures': [],
                        'detected_barriers': [], 'cognitive_load': 'BAJA', 'summary': 'Sustantivos', 'title': 'T',
                        'detected_subject': 'LEN', 'detected_level': 'MEDIA', 'handwritten': []}, 'fake'
            return {'title': 'Ficha', 'summary': 's', 'intro': 'Hola.', 'learning_goal': 'Vas a aprender.',
                    'concepts': [], 'steps': [{'title': 'Subraya', 'instruction': 'Subraya.', 'hint': 'h',
                                               'keyword': 'subraya', 'answer_type': 'ESPACIO'}],
                    'check_questions': [], 'reading_words': [{'word': 'sustantivo', 'syllables': 'sus-tan-ti-vo'}],
                    'udl_principle': 'p', 'udl_justification': 'j'}, 'fake'

        original = ai.generate_json
        ai.generate_json = fake_generate_json
        try:
            with self.assertLogs('maestro_sombra', level='INFO') as logs:
                services.process_material(material.pk, [(student.pk, student.conditions)], {student.pk: []})
        finally:
            ai.generate_json = original
        material.refresh_from_db()
        self.assertEqual(material.status, 'PROCESSED')
        self.assertTrue(sent)
        everything, logged = ' '.join(sent), ' '.join(logs.output)
        for private in (STRENGTHS, BARRIERS, SUPPORTS, 'Nota privada.', 'Estudiante de Prueba'):
            self.assertNotIn(private, everything)
            self.assertNotIn(private, logged)
