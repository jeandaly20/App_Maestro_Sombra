"""Database rules (CHECK, UNIQUE and trigger) enforced directly by PostgreSQL."""
from datetime import date

from django.db import IntegrityError, InternalError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Teacher
from apps.adaptations.models import CurricularAdaptation
from apps.evaluations.models import Evaluation
from apps.materials.models import OriginalMaterial, Subject
from apps.students.models import Student

DB_ERRORS = (IntegrityError, InternalError)


class DatabaseRulesTests(TestCase):

    def setUp(self):
        self.teacher = Teacher.objects.create_user(
            'ana@correo.com', 'Aula-Inclusiva-2026', full_name='Ana Pérez',
            accepted_terms=True, terms_accepted_at=timezone.now(),
        )
        self.math = Subject.objects.get(code='MAT')

    def new_student(self, teacher=None, **changes):
        data = {'teacher': teacher or self.teacher, 'name': 'Mateo Benítez', 'birth_date': date(2016, 5, 1),
                'grade': 'EGB_4', 'conditions': ['DYSLEXIA']}
        data.update(changes)
        return Student.objects.create(**data)

    def new_material(self):
        return OriginalMaterial.objects.create(
            teacher=self.teacher, subject=self.math, title='Fracciones', topic='Fracciones',
            input_type='TEXT', input_text='Juan tiene 3/4 de pizza...',
        )

    def assertBlocked(self, action):
        with self.assertRaises(DB_ERRORS), transaction.atomic():
            action()

    def test_subjects_are_loaded(self):
        self.assertEqual(Subject.objects.count(), 6)

    def test_student_name_is_encrypted_in_database(self):
        student = self.new_student()
        with connection.cursor() as cursor:
            cursor.execute('SELECT name FROM mst_student WHERE id = %s', [student.id])
            stored = cursor.fetchone()[0]
        self.assertNotIn('Mateo', stored)
        self.assertEqual(Student.objects.get(pk=student.pk).name, 'Mateo Benítez')
        self.assertRegex(student.pseudonym, r'^EST-[0-9A-F]{6}$')

    def test_trigger_blocks_fourth_student(self):
        for _ in range(3):
            self.new_student()
        self.assertBlocked(lambda: self.new_student())

    def test_unknown_condition_is_blocked(self):
        self.assertBlocked(lambda: self.new_student(conditions=['FLU']))

    def test_terms_need_a_date(self):
        self.assertBlocked(lambda: Teacher.objects.create_user(
            'b@correo.com', 'Aula-Inclusiva-2026', full_name='B B', accepted_terms=True))

    def test_text_material_needs_text(self):
        self.assertBlocked(lambda: OriginalMaterial.objects.create(
            teacher=self.teacher, subject=self.math, title='t', topic='t', input_type='TEXT'))

    def test_adaptation_rules(self):
        student = self.new_student()
        adaptation = CurricularAdaptation.objects.create(
            material=self.new_material(), student=student, condition='DYSLEXIA', title='Fracciones',
            content={'steps': []}, applied_adjustments=['ACCESSIBLE_FONT'],
        )
        self.assertRegex(adaptation.code, r'^ADAP-MAT-[0-9A-F]{6}$')
        self.assertBlocked(lambda: CurricularAdaptation.objects.create(
            material=adaptation.material, student=student, condition='DYSLEXIA', title='x',
            content={}, version=2, applied_adjustments=['MAGIC']))

    def test_evaluation_rules(self):
        adaptation = CurricularAdaptation.objects.create(
            material=self.new_material(), student=self.new_student(), condition='DYSLEXIA',
            title='Fracciones', content={},
        )
        self.assertBlocked(lambda: Evaluation.objects.create(adaptation=adaptation, status='FINAL'))
        Evaluation.objects.create(adaptation=adaptation, status='FINAL', autonomy_level='AUTONOMOUS',
                                  comprehension='YES', observation='Comprendió bien')
        self.assertBlocked(lambda: Evaluation.objects.create(adaptation=adaptation))

    def test_tables_use_type_prefixes(self):
        tables = set(connection.introspection.table_names())
        for table in ('cat_subject', 'mst_teacher', 'mst_student', 'trx_original_material',
                      'trx_curricular_adaptation', 'trx_evaluation'):
            self.assertIn(table, tables)

    def test_record_status_is_validated(self):
        self.assertBlocked(lambda: self.new_student(record_status='DELETED'))

    def test_inactive_student_frees_a_slot(self):
        first = self.new_student()
        for _ in range(2):
            self.new_student()
        first.record_status = 'INACTIVE'
        first.save()
        self.new_student()  # the trigger only counts active students
        self.assertBlocked(lambda: self.new_student())
        # Reactivating would exceed the limit, so the trigger also blocks it
        first.record_status = 'ACTIVE'
        self.assertBlocked(lambda: first.save())

    def test_inactive_teacher_cannot_sign_in(self):
        self.teacher.record_status = 'INACTIVE'
        self.teacher.save()
        self.assertFalse(Teacher.objects.get(pk=self.teacher.pk).is_active)
